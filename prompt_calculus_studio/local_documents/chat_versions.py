"""Read-only message lineage from the ordered, retained edit snapshots.

No text matching is used. An edit connects its recorded old message to the
message at the same position in the immediately following path, only while
the preceding message identities still match. Restore reuses retained IDs.
"""
import copy

from . import chat


class MessageVersionIndex:
    def __init__(self, payload):
        self.payload = payload
        self.turns = payload['turns']
        self.revisions = payload.get('revisions')
        self.turn_count = len(self.turns)
        self.revision_count = len(self.revisions or [])
        self.snapshots = [*(self.revisions or []), payload]
        self.active_ids = {turn['id'] for turn in self.turns}
        self.records = {}
        self.first_order = {}
        parents = {}

        def root(key):
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key

        for number, snapshot in enumerate(self.snapshots):
            for turn in snapshot['turns']:
                key = turn['id']
                parents.setdefault(key, key)
                self.first_order.setdefault(key, len(self.first_order))
                # The latest occurrence has the complete continuation retained
                # before that version was replaced, or the current live path.
                self.records[key] = (number, turn)

        for number, revision in enumerate(self.revisions or []):
            if revision['reason'] != 'edit':
                continue
            before = revision['turns']
            after = self.snapshots[number + 1]['turns']
            position = next((i for i, turn in enumerate(before) if turn['id'] == revision['turn_id']), None)
            if position is None or len(after) <= position:
                continue
            if any(before[i]['id'] != after[i]['id'] for i in range(position)):
                # Older copies that remapped each snapshot independently lost
                # this evidence. Do not guess from equal or similar content.
                continue
            left, right = root(before[position]['id']), root(after[position]['id'])
            if left != right:
                parents[right] = left

        groups = {}
        for key in parents:
            groups.setdefault(root(key), []).append(key)
        self.groups = {}
        for key in self.active_ids:
            versions = sorted(groups[root(key)], key=self.first_order.__getitem__, reverse=True)
            self.groups[key] = [key, *(version for version in versions if version != key)]

    def matches(self, payload):
        return (self.payload is payload and self.turns is payload['turns']
                and self.revisions is payload.get('revisions') and self.turn_count == len(self.turns)
                and self.revision_count == len(self.revisions or []))

    def count(self, turn_id):
        return len(self.groups.get(turn_id, []))

    def versions(self, turn_id):
        versions = self._group(turn_id)
        numbers = {key: number for number, key in enumerate(sorted(versions, key=self.first_order.__getitem__), 1)}
        result = []
        for key in versions:
            snapshot_index, turn = self.records[key]
            current = key == turn_id
            result.append(dict(id=key, turn_id=key, current=current, number=numbers[key], text=turn['text'],
                               attachments=copy.deepcopy(turn.get('attachments', [])),
                               revision_id=self._revision_id(snapshot_index),
                               label='目前版本' if current else f'版本 {numbers[key]}'))
        return result

    def _group(self, turn_id):
        if turn_id not in self.groups:
            raise ValueError('找不到這則訊息。')
        return self.groups[turn_id]

    def _revision_id(self, snapshot_index):
        return self.snapshots[snapshot_index]['id'] if snapshot_index < self.revision_count else None

    def selected(self, turn_id, version_id):
        if version_id not in self._group(turn_id):
            raise ValueError('找不到這則訊息的所選版本。')
        return self.records[version_id][0]

    def branch_payload(self, turn_id, version_id):
        number = self.selected(turn_id, version_id)
        snapshot = self.snapshots[number]
        result = dict(version=1, draft='', system_prompt=snapshot['system_prompt'],
                      turns=copy.deepcopy(snapshot['turns']))
        if self.revisions is not None:
            # Keep the history which preceded this exact path, not later edits
            # whose successor would incorrectly become the selected old path.
            result['revisions'] = copy.deepcopy(self.revisions[:number])
        chat.validate_payload(result)
        return result, self._revision_id(number)

"""Model-facing excerpts, separate from the unchanged source-read receipts."""
from .sources import digest


def clip(text, limit):
    raw = text.encode('utf8')
    if len(raw) <= limit:
        return text, False
    prefix = raw[:limit].decode('utf8', errors='ignore')
    # Avoid presenting half a source line as a complete line.
    cut = prefix.rfind('\n')
    return (prefix[:cut] if cut > limit // 3 else prefix), True


class ToolTranscript:
    def __init__(self):
        self.exact, self.lines = {}, {}
        self.messages = []

    def project(self, call_id, event, raw, evidence_id):
        fingerprint = (event['operation'], event['target'], event['sha256'], digest(raw.encode('utf8')))
        prefix = f'[{evidence_id}: {event["operation"]} {event["target"]}; SHA256 {event["sha256"] or "n/a"}]\n'
        if fingerprint in self.exact:
            body = f'[Identical result already shown in {self.exact[fingerprint]}; no new evidence.]'
        else:
            body = raw
            if event['operation'] == 'read_file':
                path = event['target'].rsplit(':', 1)[0]
                rows, omitted = [], []
                for line in raw.splitlines():
                    number, colon, text = line.partition(': ')
                    key = (path, event['sha256'], number, text)
                    if colon and number.isdecimal() and key in self.lines:
                        omitted.append(self.lines[key])
                    else:
                        rows.append(line)
                body = '\n'.join(rows)
                if omitted:
                    body = '[Overlapping lines already shown in ' + ', '.join(dict.fromkeys(omitted)) + '.]\n' + body
            # Skills and registered rules remain complete; if too large, actual
            # token preflight closes the read phase before any model submission.
            if event['operation'] in ('read_file', 'search_files'):
                body, clipped = clip(body, 8000)
                if clipped:
                    body += '\n[Remaining text omitted from this projection. Raw read retained; request a narrower line range if necessary.]'
            if event['operation'] == 'read_file':
                path = event['target'].rsplit(':', 1)[0]
                for line in body.splitlines():
                    number, colon, text = line.partition(': ')
                    if colon and number.isdecimal():
                        self.lines[(path, event['sha256'], number, text)] = evidence_id
            self.exact[fingerprint] = evidence_id
        content = prefix + body
        message = dict(role='tool', tool_call_id=call_id, content=content)
        self.messages.append(message)
        return message

    def compact(self, limit):
        """Only for closing: preserve literal prefixes and mark every omission."""
        changed = {}
        for message in self.messages:
            content, clipped = clip(message['content'], limit)
            if clipped:
                content += '\n[Final-budget excerpt; remaining raw text is archived, not visible here. Do not infer omitted details.]'
                message['content'] = content
                changed[message['tool_call_id']] = content
        return changed

    def answer_evidence(self):
        return ('Read-only evidence already obtained (literal excerpts; no further reads):\n\n'
                + '\n\n'.join(m['content'] for m in self.messages))

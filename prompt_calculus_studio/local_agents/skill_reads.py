"""Track delivered Skill text, not semantic compliance or automatic permissions."""
import re


def relative_path(value):
    """Canonicalize a folder-relative name without resolving outside the snapshot."""
    value = value.replace('\\', '/')
    if not value or value.startswith('/') or ':' in value or '..' in value.split('/'):
        raise ValueError('技能附檔必須使用技能內相對路徑，不可跨出根目錄。')
    path = '/'.join(part for part in value.split('/') if part not in ('', '.'))
    if not path:
        raise ValueError('請指定技能內的檔案。')
    return path


class SkillReads:
    def __init__(self, skills, files):
        self.skills, self.files = skills, files
        self.read = {}
        self.unresolved = {}

    def referenced(self, identity, path):
        # Literal reference detection only. Whether a reference is mandatory or
        # conditional remains a task/rule decision, never inferred by a regex.
        pattern = r'(?<![\w./-])(?:\./)?' + re.escape(path) + r'(?![\w./-])'
        return any(re.search(pattern, text.replace('\\', '/'))
                   for (skill, _), text in self.read.items() if skill == identity)

    def delivered(self, identity, path, content):
        self.read[identity, path] = content
        self.unresolved.pop((identity, path), None)

    def failed(self, operation, args):
        """Remember failed attempts to read references in already delivered Skills.

        A speculative project lookup or a misspelled path is not a dependency.
        A later successful read from the correct Skill scope resolves the failure.
        """
        if operation not in ('read_skill', 'read_file') or not isinstance(args, dict):
            return
        try:
            path = relative_path(args['path'])
        except (KeyError, AttributeError, ValueError):
            return
        for identity, skill in self.skills.items():
            if operation == 'read_skill' and args.get('skill') not in (identity, skill['name']):
                continue
            if (identity, path) not in self.read and self.referenced(identity, path):
                self.unresolved[identity, path] = skill['name'] + '/' + path

    def catalog(self, identity):
        paths = list(self.files[identity])
        rows = [f'{path} [{"read" if (identity, path) in self.read else "not read"}]'
                for path in paths[:32]]
        if len(paths) > 32:
            rows.append('[Index shortened; other paths referenced by the Skill can also be requested.]')
        return ('[Host Skill file index: names only, not attachment contents]\n' + '\n'.join(rows)
                + '\nUse read_skill with this same Skill and the exact folder-relative path. '
                'Read unconditional requirements and applicable conditional references before using them. '
                'A listed file has not been read merely because it is available. Do not read irrelevant attachments.')

    def unread_references(self):
        return [self.skills[identity]['name'] + '/' + path
                for identity, paths in self.files.items() for path in paths
                if (identity, path) not in self.read and self.referenced(identity, path)]

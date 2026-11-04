"""Send a read-only task to an already running PCS workspace. No Qt or engine."""
import argparse
import json
from pathlib import Path
import re
import stat
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.connector_host import ConnectorError, exchange_pipe
from prompt_calculus_studio.local_agents.delegation_protocol import (
    MAX_REQUEST, MAX_RESPONSE, PIPE_NAME, DelegationError, discovery_directory, validate)


def discover(directory=None):
    directory = Path(directory) if directory is not None else discovery_directory()
    found = []
    for path in sorted(directory.glob('*.json'))[:32]:
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 16384
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                continue
            with path.open('rb') as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                continue
            item = json.loads(raw)
            if (not isinstance(item, dict) or type(item.get('version')) is not int or item['version'] != 1
                    or not all(isinstance(item.get(k), str) for k in
                               ('instance_id', 'connection_id', 'pipe_name', 'token', 'display_name'))
                    or not re.fullmatch(r'[0-9a-f]{24}', item['instance_id'])
                    or not re.fullmatch(r'[0-9a-f]{32}', item['connection_id'])
                    or not re.fullmatch(r'[0-9a-f]{64}', item['token'])
                    or item['pipe_name'] != 'pcs-agent-'+item['instance_id']
                    or not 1 <= len(item['display_name']) <= 200):
                continue
            found.append(item)
        except (ValueError, OSError, RecursionError):
            continue
    return found


class AgentClient:
    def __init__(self, directory=None, exchange=exchange_pipe):
        self.directory, self.exchange = directory, exchange

    def call(self, instance, command, fields=None, *, timeout=10.):
        request = dict(version=1, command=command, request_id=uuid.uuid4().hex,
                       instance_id=instance['instance_id'], connection_id=instance['connection_id'],
                       token=instance['token'], **(fields or {}))
        validate(request)
        response = self.exchange(instance['pipe_name'], request, timeout, pattern=PIPE_NAME, max_bytes=MAX_RESPONSE)
        if (not isinstance(response, dict) or response.get('request_id') != request['request_id']
                or type(response.get('ok')) is not bool):
            raise DelegationError('invalid_response', '無法核對 PCS 委派回應。')
        return response

    def instances(self):
        live = []
        for instance in discover(self.directory):
            try:
                response = self.call(instance, 'catalog', timeout=.3)
                if response['ok']:
                    live.append({k: instance[k] for k in ('instance_id', 'display_name')})
            except (DelegationError, ConnectorError, OSError):
                pass
        return dict(ok=True, result=dict(instances=live))

    def request(self, instance_id, command, fields=None):
        instance = next((i for i in discover(self.directory) if i['instance_id'] == instance_id), None)
        if instance is None:
            raise DelegationError('instance_unavailable', '找不到已啟用的 PCS 工作區，請先開啟專案的本機委派。')
        return self.call(instance, command, fields)


def main(argv=None):
    parser = argparse.ArgumentParser(description='PCS 本機唯讀委派')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('instances', help='查詢已啟用的 PCS 工作區')
    for command in ('catalog', 'submit', 'status', 'stop', 'result'):
        part = sub.add_parser(command)
        part.add_argument('--instance', required=True)
        if command == 'submit':
            part.add_argument('--project', required=True)
            part.add_argument('--agent', required=True)
            part.add_argument('--key', required=True)
            content = part.add_mutually_exclusive_group(required=True)
            content.add_argument('--task')
            content.add_argument('--task-file', type=Path)
        elif command != 'catalog':
            part.add_argument('--task-id', required=True)
    args = parser.parse_args(argv)
    try:
        client = AgentClient()
        fields = {}
        if args.command == 'submit':
            task = args.task
            if args.task_file:
                with args.task_file.open('rb') as stream:
                    raw = stream.read(MAX_REQUEST+1)
                if len(raw) > MAX_REQUEST:
                    raise DelegationError('request_too_large', '工作文字檔超過大小限制。')
                task = raw.decode('utf-8-sig')
            fields = dict(project_id=args.project, agent_id=args.agent, task=task, idempotency_key=args.key)
        elif hasattr(args, 'task_id'):
            fields = dict(task_id=args.task_id)
        response = client.instances() if args.command == 'instances' else client.request(args.instance, args.command, fields)
    except (DelegationError, ConnectorError) as exc:
        response = dict(ok=False, error=dict(code=exc.code, message=str(exc)))
    except (OSError, ValueError):
        response = dict(ok=False, error=dict(code='input_unavailable', message='無法讀取工作文字或連線設定。'))
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return 0 if response['ok'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

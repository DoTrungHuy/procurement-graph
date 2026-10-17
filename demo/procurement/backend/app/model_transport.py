"""Normalize JSON and SSE chat completions without changing extraction rules."""
from __future__ import annotations

import json

import httpx


def _sse_data(lines):
    parts = []
    for line in lines:
        if not line:
            if parts:
                yield '\n'.join(parts)
                parts = []
        elif line.startswith('data:'):
            parts.append(line[5:].removeprefix(' '))
    if parts:
        yield '\n'.join(parts)


def _remove_end_markers(content: str) -> tuple[str, int]:
    # WorkBuddy DS4.1 can leak this control token between JSON values. Keep
    # literal occurrences inside evidence/string fields and retain the raw text.
    marker = '<|endoftext|>'
    result = []
    quoted = escaped = False
    removed = index = 0
    while index < len(content):
        char = content[index]
        if not quoted and content.startswith(marker, index):
            removed += 1
            index += len(marker)
            continue
        result.append(char)
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        index += 1
    return ''.join(result), removed


def read_model_response(response: httpx.Response) -> dict:
    """Return the same envelope for ordinary JSON and forced-stream gateways.

    A finish event is required: a disconnected stream must never become a
    successful cached response, even if its partial text happens to be JSON.
    """
    if response.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'text/event-stream':
        return response.json()

    envelope = {'object': 'chat.completion', 'usage': {}}
    content = []
    finish_reason = None
    for raw in _sse_data(response.iter_lines()):
        if raw == '[DONE]':
            break
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get('error'):
            raise ValueError('模型流返回错误事件')
        for key in ('id', 'model', 'created'):
            if event.get(key) is not None:
                envelope[key] = event[key]
        if isinstance(event.get('usage'), dict):
            envelope['usage'].update(event['usage'])
        for choice in event.get('choices', []):
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta') or choice.get('message') or {}
            piece = delta.get('content')
            if piece is not None:
                if not isinstance(piece, str):
                    raise ValueError('模型流文本类型无效')
                content.append(piece)
            if choice.get('finish_reason'):
                finish_reason = choice['finish_reason']
    if not finish_reason:
        raise ValueError('模型流在完成事件之前中断')
    if finish_reason not in {'stop', 'length'}:
        raise ValueError('模型流未正常返回抽取文本')
    raw_content = ''.join(content)
    text, removed = _remove_end_markers(raw_content)
    envelope['choices'] = [{'index': 0, 'finish_reason': finish_reason,
                            'message': {'role': 'assistant', 'content': text}}]
    envelope['_transport'] = {'protocol': 'sse', 'removed_end_markers': removed}
    if removed:
        envelope['_transport']['raw_content'] = raw_content
    return envelope

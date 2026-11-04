"""Codex exec completed-turn usage normalization for the project harness."""
from .usage import number


def codex_usage(raw):
    if not isinstance(raw, dict):
        return {}
    details = raw.get('input_tokens_details')
    cached = raw.get('cached_input_tokens', details.get('cached_tokens') if isinstance(details,dict) else None)
    return {'input_tokens':number(raw.get('input_tokens')), 'cached_input_tokens':number(cached), 'output_tokens':number(raw.get('output_tokens'))}

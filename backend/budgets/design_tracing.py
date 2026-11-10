"""Opt-in LangSmith traces with explicit, amount-free payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import os

CONFIG_KEYS = ('LANGSMITH_TRACING', 'LANGSMITH_API_KEY', 'LANGSMITH_ENDPOINT',
               'LANGSMITH_PROJECT', 'LANGSMITH_WORKSPACE_ID', 'LANGSMITH_AGENT_ID',
               'LANGSMITH_AGENT_ENVIRONMENT')
_active = ContextVar('budget_design_trace', default=None)


def _start(config, status):
    if str(config.get('LANGSMITH_TRACING', '')).lower() != 'true':
        return None, None
    if not config.get('LANGSMITH_API_KEY'):
        status['status'] = 'missing_api_key'
        return None, None
    client = None
    try:
        from langsmith import Client, agent_address
        from langsmith.run_trees import RunTree
        from urllib3.util.retry import Retry

        def failed(error):
            status['status'] = f'failed:{type(error).__name__}'

        client = Client(api_key=config['LANGSMITH_API_KEY'],
                        api_url=config.get('LANGSMITH_ENDPOINT') or 'https://api.smith.langchain.com',
                        workspace_id=config.get('LANGSMITH_WORKSPACE_ID') or None,
                        timeout_ms=1500, retry_config=Retry(total=0),
                        omit_traced_runtime_info=True, tracing_error_callback=failed)
        agent_id = config.get('LANGSMITH_AGENT_ID')
        destination = ({'address': agent_address(agent_id, config.get('LANGSMITH_AGENT_ENVIRONMENT') or 'development')}
                       if agent_id else {'project_name': config.get('LANGSMITH_PROJECT') or 'budget-design'})
        root = RunTree(name='budget_design', run_type='chain', inputs={}, ls_client=client,
                       extra={'metadata': {'payload_policy': 'no_amounts_or_prompts'}}, **destination)
        status.update(status='recording', trace_id=str(root.id))
        return root, client
    except Exception as error:
        status['status'] = f'unavailable:{type(error).__name__}'
        return None, client


def trace_budget(function):
    @wraps(function)
    def wrapped(*args, trace_config=None, **kwargs):
        config = trace_config if trace_config is not None else {k: os.environ.get(k, '') for k in CONFIG_KEYS}
        status = {'status': 'disabled'}
        root, client = _start(config, status)
        token = _active.set((root, status) if root else None)
        result, error_type = None, None
        try:
            result = function(*args, **kwargs)
            return {**result, 'langsmith': status}
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            _active.reset(token)
            try:
                if root:
                    # Never pass function arguments, financial outputs or exception messages.
                    outputs = {k: result[k] for k in ('selection_source', 'fallback') if result and k in result}
                    root.end(outputs=outputs, error=error_type or outputs.get('fallback'))
                    # Buffer until budget calculation finishes: network waits cannot consume its deadline.
                    if status['status'] == 'recording':
                        status['status'] = 'queued'
                    root.post(exclude_child_runs=False)
                    client.flush(timeout=2)
            except Exception as error:
                status['status'] = f'failed:{type(error).__name__}'
            finally:
                if client:
                    try:
                        client.close(timeout=0)
                    except Exception as error:
                        status['status'] = f'failed:{type(error).__name__}'
    return wrapped


@contextmanager
def span(name, run_type, *, inputs=None, metadata=None):
    """Call sites supply only allowlisted labels; outputs are filled explicitly."""
    active = _active.get()
    child, error_type, outputs = None, None, {}
    if active:
        try:
            child = active[0].create_child(name, run_type, inputs=deepcopy(inputs or {}),
                                           extra={'metadata': metadata or {}})
        except Exception as error:
            active[1]['status'] = f'failed:{type(error).__name__}'
    try:
        yield outputs
    except Exception as error:
        error_type = type(error).__name__
        raise
    finally:
        if child:
            try:
                child.end(outputs=outputs, error=error_type)
            except Exception as error:
                active[1]['status'] = f'failed:{type(error).__name__}'


def token_usage(usage):
    if not isinstance(usage, dict):
        return {}
    names = {'prompt_tokens': 'input_tokens', 'completion_tokens': 'output_tokens', 'total_tokens': 'total_tokens'}
    return {target: usage[source] for source, target in names.items()
            if type(usage.get(source)) is int and usage[source] >= 0}

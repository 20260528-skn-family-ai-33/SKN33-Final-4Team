"""Explicit opt-in tracing for adjustment turns, after the service consent gate."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from hashlib import sha256
import logging
import os
from uuid import uuid4

log = logging.getLogger(__name__)
_active = ContextVar('budget_adjustment_trace', default=None)


@lru_cache(maxsize=4)
def _client(key, endpoint, workspace):
    from langsmith import Client
    from urllib3.util.retry import Retry
    return Client(api_key=key, api_url=endpoint or 'https://api.smith.langchain.com',
                  workspace_id=workspace or None, timeout_ms=1500, retry_config=Retry(total=0),
                  omit_traced_runtime_info=True,
                  tracing_error_callback=lambda error: _failed(error))


def _failed(error):
    log.warning('Budget LangSmith tracing failed (%s)', type(error).__name__)


def _start(question, conversation):
    if (os.getenv('BUDGET_ADJUST_LANGSMITH', '').lower() != 'true'
            or os.getenv('LANGSMITH_TRACING', '').lower() != 'true'):
        return None
    key = os.getenv('LANGSMITH_API_KEY')
    if not key:
        log.warning('Budget LangSmith tracing needs LANGSMITH_API_KEY')
        return None
    try:
        from langsmith.run_trees import RunTree
        client = _client(key, os.getenv('LANGSMITH_ENDPOINT', ''), os.getenv('LANGSMITH_WORKSPACE_ID', ''))
        run = RunTree(name='budget_adjustment', run_type='chain', inputs={'question': question},
                      project_name=os.getenv('BUDGET_ADJUST_LANGSMITH_PROJECT') or 'budget-adjustment',
                      ls_client=client, tags=['budget-adjustment'],
                      extra={'metadata': {'session_id': sha256(str(conversation).encode()).hexdigest() if conversation else uuid4().hex,
                                          'payload_policy': 'explicit_opt_in_prompts_and_outputs'}})
        run.post()
        return run
    except Exception as error:
        _failed(error)
        return None


@contextmanager
def _span(run):
    output, error_type = {}, None
    try:
        yield output
    except BaseException as error:
        error_type = type(error).__name__
        raise
    finally:
        if run is not None:
            try:
                run.end(outputs=output, error=error_type)
                run.patch()
            except Exception as error:
                _failed(error)


def trace_turn(request, conversation, call):
    run = _start(request.get('text'), conversation)
    token = _active.set(run)
    try:
        with _span(run) as output:
            result = call()
            if run is not None:
                output.update(status=result.get('status'), reply=result.get('reply'))
            return result
    finally:
        _active.reset(token)


def trace_llm(client, system, user, timeout):
    parent, child = _active.get(), None
    if parent is not None:
        try:
            from .live import schema_for
            role, _ = schema_for(system)
            child = parent.create_child(name=role, run_type='llm',
                inputs={'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]},
                extra={'metadata': {'ls_provider': 'openai', 'ls_model_name': client.model}})
            child.post()
        except Exception as error:
            _failed(error)
            child = None
    with _span(child) as output:
        result = client.complete(system, user, timeout)
        if child is not None:
            output['output'] = result
        return result

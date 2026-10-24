#!/usr/bin/env python3
"""기능별 계약 JSON → 공유용 OpenAPI YAML. Django 서버나 DB 없이 실행한다."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from config.openapi import build_contract, operations  # noqa: E402


def canonical_path(path):
    return re.sub(r"\{[^}]+\}|<[^>]+>", "{id}", path)


def check_inventory(schema):
    """수기로 옮긴 계약의 API 누락/추가를 원본 목차와 대조한다."""
    text = (ROOT / "docs/api/README.md").read_text()
    expected = set()
    for line in text.splitlines():
        if not re.match(r"\|\s*\d+\s*\|", line):
            continue
        match = re.search(r"`(GET|POST|PUT|PATCH|DELETE) (/api/[^`]+)`", line)
        if match:
            expected.add((match[1].lower(), canonical_path(match[2])))
    actual = {(method, canonical_path(path)) for path, method, _ in operations(schema)}
    if actual != expected:
        raise ValueError(f"원본 목차와 다름: 누락={sorted(expected - actual)}, 추가={sorted(actual - expected)}")
    ids = [op["operationId"] for _, _, op in operations(schema)]
    if len(ids) != len(set(ids)):
        raise ValueError("operationId 중복")


def validate_schema(schema):
    try:
        from openapi_spec_validator import validate
        from openapi_schema_validator import OAS30Validator, oas30_format_checker
    except ImportError as exc:
        raise SystemExit("검증 의존성이 필요합니다: python -m pip install -r requirements-docs.txt") from exc
    validate(schema)
    count = 0

    def check(value, definition, location):
        nonlocal count
        validator = OAS30Validator({**definition, "components": schema["components"]}, format_checker=oas30_format_checker)
        issues = sorted(validator.iter_errors(value), key=lambda e: str(e.path))
        if issues:
            details = "; ".join(f"{list(e.path)}: {e.message}" for e in issues)
            raise ValueError(f"예시 불일치: {location}: {details}")
        count += 1

    def media_examples(media, location):
        definition = media.get("schema", {})
        if "example" in media:
            check(media["example"], definition, location)
        for name, ex in media.get("examples", {}).items():
            if "value" in ex:
                check(ex["value"], definition, location + "/" + name)

    for path, method, op in operations(schema):
        label = method.upper() + " " + path
        for media in op.get("requestBody", {}).get("content", {}).values():
            media_examples(media, label + " request")
        for status, response in op["responses"].items():
            for media in response.get("content", {}).values():
                media_examples(media, label + " " + status)
        for param in op.get("parameters", []):
            if "$ref" in param:
                param = schema["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
            if "example" in param:
                check(param["example"], param["schema"], label + " " + param["name"])
    # 컴포넌트에 직접 넣은 예시도 검사한다.
    def walk(value, location):
        if isinstance(value, dict):
            if "example" in value and any(k in value for k in ("type", "$ref", "allOf", "oneOf")):
                check(value["example"], value, location)
            for key, child in value.items():
                if key not in {"example", "examples"}:
                    walk(child, location + "/" + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, location + "/" + str(i))
    walk(schema.get("components", {}).get("schemas", {}), "components/schemas")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="저장한 YAML이 소스와 같은지 확인")
    parser.add_argument("--validate", action="store_true", help="OpenAPI 구조와 요청·응답 예시 검증")
    args = parser.parse_args()
    try:
        import yaml
        schema = build_contract()
        check_inventory(schema)
        examples = validate_schema(schema) if args.validate else None
        content = "# 자동 생성: python scripts/build_openapi.py\n# 편집: docs/openapi/parts/*.json 및 backend/config/openapi.py\n" + yaml.safe_dump(schema, allow_unicode=True, sort_keys=False, width=110)
        artifacts = {
            ROOT / "docs/openapi/openapi.yaml": content,
            ROOT / "docs/openapi/openapi.json": json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        }
        for target, rendered in artifacts.items():
            if args.check:
                if not target.exists() or target.read_text() != rendered:
                    raise ValueError(f"{target.name}이 최신이 아닙니다. python scripts/build_openapi.py 실행 후 확인하세요.")
            else:
                target.write_text(rendered)
        message = f"OpenAPI {len(list(operations(schema)))}개 API · {len(schema['components']['schemas'])}개 모델"
        if examples is not None:
            message += f" · 예시 {examples}개 검증 통과"
        print(message + (" · 최신 파일 확인" if args.check else " · docs/openapi/openapi.json·yaml 저장"))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()

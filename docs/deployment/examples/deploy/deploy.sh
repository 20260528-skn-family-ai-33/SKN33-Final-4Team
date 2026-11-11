#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd /opt/moneycushion
exec 9>/opt/moneycushion/deploy.lock
flock -w 900 9

export IMAGE_TAG="${1:?Usage: deploy.sh IMAGE_TAG [rollback]}"
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]] || { echo 'Invalid image tag'; exit 1; }
mode="${2:-deploy}"
[[ "$mode" == deploy || "$mode" == rollback ]] || exit 1
# This file contains only administrator-controlled, non-secret shell-safe values.
set -a
source /opt/moneycushion/config.env
set +a
test -s app.env
test -s models/spending_category_svm.joblib
test -s models/label_conflicts.csv
# 예산 분배를 켠 경우에만 CatBoost 모델을 요구한다 (없으면 모든 추천이 needs_data로 막힘)
if grep -qx 'BUDGET_DESIGN_ENABLED=true' app.env; then
    test -s models/budget_allocation.joblib
fi
compose_file=/opt/moneycushion/deploy/compose.yml
if [[ "$mode" == rollback ]]; then
    compose_file="/opt/moneycushion/releases/$IMAGE_TAG/compose.yml"
    test -f "$compose_file"
fi
compose() {
    docker compose --project-name moneycushion --project-directory /opt/moneycushion \
        --env-file /opt/moneycushion/config.env -f "$compose_file" "$@"
}
trap 'echo "Deployment failed. Inspect containers and RDS migration state; no database rollback was attempted." >&2' ERR
aws ecr get-login-password --region "$AWS_REGION" |
    docker login --username AWS --password-stdin "$ECR_REGISTRY"
compose pull
if [[ "$mode" == deploy ]]; then
    compose run --rm --no-deps backend python manage.py check --deploy --fail-level ERROR
    compose run --rm --no-deps backend python manage.py migrate --noinput
    # 횟수 제한용 DB 캐시 표 (이미 있으면 건너뜀)
    compose run --rm --no-deps backend python manage.py createcachetable
fi
compose up -d --wait --wait-timeout 240
result=$(mktemp)
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
    --max-time 15 "https://$SITE_DOMAIN/api/health/" -o "$result"
python3 - "$result" "$IMAGE_TAG" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    result = json.load(stream)
assert result['status'] == 'ok' and result['release'] == sys.argv[2], result
PY
rm -f "$result"
mkdir -p "releases/$IMAGE_TAG"
if [[ "$mode" == deploy ]]; then
    cp "$compose_file" "releases/$IMAGE_TAG/compose.yml"
fi
if [[ -f current-tag ]]; then
    cp current-tag previous-tag
fi
printf '%s\n' "$IMAGE_TAG" > current-tag.tmp
mv current-tag.tmp current-tag
echo "Deployed $IMAGE_TAG"

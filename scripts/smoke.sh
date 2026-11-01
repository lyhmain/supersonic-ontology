#!/usr/bin/env bash
# 冒烟:健康/页面/profile 列表/一条问数
set -e
BASE=${1:-http://localhost:9102}
curl -sf $BASE/api/health && echo
curl -sf $BASE/api/ui/health && echo
curl -sf $BASE/api/ui/profiles && echo
curl -sf -X POST $BASE/api/nl -H 'Content-Type: application/json'      -d '{"question":"昨天营业额是多少","profile":"demo_tea"}' && echo

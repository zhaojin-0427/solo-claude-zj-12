"""排演检查：在动手前发现条件漏洞，在分析时定位数据问题。

每条 issue：{"code", "level": warn|error, "msg", "round_seq"? , "factor_id"?}
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict


def _combo_key(combo) -> str:
    if isinstance(combo, str):
        combo = json.loads(combo)
    return json.dumps(combo, ensure_ascii=False, sort_keys=True)


def check_design(state: dict) -> list[dict]:
    factors = state.get("factors", [])
    rounds = state.get("rounds", [])
    materials = state.get("materials", [])
    slots = state.get("slots", [])
    inquiry = state.get("inquiry", state)
    repeats = int(inquiry.get("repeats", 1) or 1)
    issues: list[dict] = []

    ivs = [f for f in factors if f["kind"] == "independent"]
    dvs = [f for f in factors if f["kind"] == "dependent"]
    cvs = [f for f in factors if f["kind"] == "controlled"]
    unassigned = [f for f in factors if f["kind"] == "unassigned"]

    # 1. 变量归类
    for f in unassigned:
        issues.append({"code": "W_UNCLASSIFIED", "level": "warn",
                       "msg": f"因素「{f['name']}」还没有归类，先想清楚它是自变量、因变量还是控制变量。",
                       "factor_id": f["id"]})
    if not ivs:
        issues.append({"code": "W_NO_IV", "level": "error",
                       "msg": "还没有自变量：你打算每次改变什么来观察效果？"})
    if not dvs:
        issues.append({"code": "W_NO_DV", "level": "error",
                       "msg": "还没有因变量：每次实验要测量什么、记下什么数据？"})
    for f in ivs:
        if len(f.get("levels") or []) < 2:
            issues.append({"code": "W_IV_LEVELS", "level": "error",
                           "msg": f"自变量「{f['name']}」至少需要 2 个水平才能做对比。",
                           "factor_id": f["id"]})

    # 2. 控制变量遗漏
    if not cvs:
        issues.append({"code": "W_NO_CONTROL", "level": "warn",
                       "msg": "没有设置任何控制变量。除了自变量，还有哪些条件必须每轮保持相同？"})
    else:
        missing = [f["name"] for f in cvs if not (f.get("levels") and str(f["levels"][0]).strip())]
        for name in missing:
            issues.append({"code": "W_CONTROL_VALUE", "level": "warn",
                           "msg": f"控制变量「{name}」没有填写固定值，无法保证每轮一致。"})

    # 3. 重复不均
    if rounds:
        counts = Counter(_combo_key(r["combo"]) for r in rounds)
        values = set(counts.values())
        if len(values) > 1 or any(v != repeats for v in values):
            issues.append({"code": "W_UNEVEN_REPEATS", "level": "warn",
                           "msg": f"各水平组合的重复次数不均（{dict(counts)}），目标是每组合 {repeats} 次。"})

    # 4. 同轮改动多个因素：比较相邻轮次，改变的自变量 > 1
    if len(ivs) >= 2 and len(rounds) >= 2:
        ordered = sorted(rounds, key=lambda r: r["seq"])
        for prev, cur in zip(ordered, ordered[1:]):
            c1 = prev["combo"] if isinstance(prev["combo"], dict) else json.loads(prev["combo"])
            c2 = cur["combo"] if isinstance(cur["combo"], dict) else json.loads(cur["combo"])
            changed = [n for n in c1 if str(c1.get(n)) != str(c2.get(n))]
            if len(changed) > 1:
                issues.append({"code": "W_MULTI_IV", "level": "warn",
                               "msg": f"第 {cur['seq']} 轮与第 {prev['seq']} 轮相比，同时改动了 "
                                      f"{'、'.join(changed)}，无法判断是哪个因素起作用。",
                               "round_seq": cur["seq"]})

    # 5. 资源冲突
    # 5a. 同一时段被安排的轮次超过容量
    slot_usage = Counter(r.get("slot_id") for r in rounds if r.get("slot_id"))
    for s in slots:
        used = slot_usage.get(s["id"], 0)
        if used > int(s.get("capacity", 1)):
            issues.append({"code": "W_SLOT_OVER", "level": "error",
                           "msg": f"时段「{s['label']}」安排了 {used} 轮，但容量只有 {s['capacity']}。"})
    no_slot = [r["seq"] for r in rounds if not r.get("slot_id")]
    if no_slot:
        issues.append({"code": "W_SLOT_SHORT", "level": "error",
                       "msg": f"第 {'、'.join(map(str, no_slot))} 轮排不进任何可用时段，时段容量不足。"})

    # 5b. 材料份数不足（总需求 = 每轮消耗 × 轮数）
    n_rounds = len(rounds)
    for m in materials:
        need = float(m.get("per_round", 0)) * n_rounds
        stock = float(m.get("stock", 0))
        if n_rounds and need > stock + 1e-9:
            issues.append({"code": "W_MATERIAL", "level": "error",
                           "msg": f"材料「{m['name']}」共需 {need:g}{m.get('unit','')}，"
                                  f"库存只有 {stock:g}{m.get('unit','')}，缺 {need-stock:g}。"})
    return issues


def check_data(state: dict) -> list[dict]:
    """执行/分析阶段：单位混用、缺测、剔除异常值。"""
    factors = state.get("factors", [])
    dvs = {f["id"]: f for f in factors if f["kind"] == "dependent"}
    rounds = state.get("rounds", [])
    measurements = state.get("measurements", [])
    issues: list[dict] = []

    by_round: dict[int, list[dict]] = defaultdict(list)
    for m in measurements:
        by_round[int(m["round_id"])].append(m)

    seq_by_id = {r["id"]: r["seq"] for r in rounds}

    for r in rounds:
        # 只对已完成/已锁定的轮次追究缺测；未做的轮次不报
        if not (r.get("locked") or r.get("done")):
            continue
        rms = by_round.get(r["id"], [])
        # 每个因变量应有一条测量记录（允许值为空但要注明原因）
        for dv_id, dv in dvs.items():
            row = next((x for x in rms if x.get("dv_id") == dv_id), None)
            standard_unit = (dv.get("unit") or "").strip()
            if row is None:
                issues.append({"code": "W_MISSING", "level": "warn",
                               "msg": f"第 {r['seq']} 轮缺少因变量「{dv['name']}」的测量值（缺测）。",
                               "round_seq": r["seq"]})
                continue
            if row.get("value") is None:
                issues.append({"code": "W_MISSING", "level": "warn",
                               "msg": f"第 {r['seq']} 轮「{dv['name']}」缺测，原因："
                                      f"{row.get('anomaly') or '未注明'}。",
                               "round_seq": r["seq"]})
            if row.get("excluded"):
                issues.append({"code": "W_EXCLUDED", "level": "warn",
                               "msg": f"第 {r['seq']} 轮「{dv['name']}」的异常值被直接剔除（原因："
                                      f"{row.get('anomaly') or '未注明'}）。该值不进入均值与极差，"
                                      f"但建议在报告中说明。",
                               "round_seq": r["seq"]})
            used_unit = (row.get("unit") or "").strip()
            if row.get("value") is not None and standard_unit and used_unit and used_unit != standard_unit:
                issues.append({"code": "W_UNIT_MIX", "level": "error",
                               "msg": f"第 {r['seq']} 轮「{dv['name']}」单位为「{used_unit}」，"
                                      f"与设定单位「{standard_unit}」不一致，不能直接一起平均。",
                               "round_seq": r["seq"]})
    return issues

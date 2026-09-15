"""平衡随机排演：固定种子 + 完整 RNG 事件记录（可回放）。

排演策略
--------
1. 取全部自变量的水平做笛卡尔积，得到「水平组合」清单；
2. 每个组合安排 repeats 次，共 repeats 个「区组」，每区组含全部组合各一次，
   对每个区组用固定种子的随机数做 Fisher–Yates 洗牌，从而保证：
   - 每个组合出现次数相等（平衡）；
   - 顺序随机、且换种子（generation）后可复现；
3. 已锁定的已完成轮次保留在原位，未完成位置由剩余的「组合次数配额」洗牌后填入，
   若配额因此不均，则给出 W_UNEVEN_REPEATS 提示；
4. 轮次按可用时段容量循环分配；容量不足时时段为空并报资源冲突。
"""
from __future__ import annotations

import json
import random
from itertools import product
from typing import Any


class RngRecorder:
    """包一层 random.Random，记录每次洗牌的每一次交换，供回放核对。"""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.events: list[dict[str, Any]] = []

    def shuffle(self, items: list[Any], label: str) -> list[Any]:
        """Fisher–Yates 洗牌并记录 (位置, 选中的随机索引)。"""
        picks: list[int] = []
        for i in range(len(items) - 1, 0, -1):
            j = self.rng.randrange(i + 1)
            picks.append(j)
            items[i], items[j] = items[j], items[i]
        self.events.append({"type": "shuffle", "label": label, "n": len(items), "picks": picks})
        return items


def _combo_key(combo: dict[str, str]) -> str:
    """组合的稳定字符串键（自变量名按字典序）。"""
    return json.dumps(combo, ensure_ascii=False, sort_keys=True)


def build_combos(ivs: list[dict]) -> list[dict[str, str]]:
    """按自变量 ord 顺序做水平笛卡尔积。"""
    ivs = sorted(ivs, key=lambda f: f.get("ord", 0))
    names = [f["name"] for f in ivs]
    level_lists = [list(f.get("levels") or []) for f in ivs]
    if not names or any(len(ls) == 0 for ls in level_lists):
        return []
    return [dict(zip(names, vals)) for vals in product(*level_lists)]


def schedule(
    ivs: list[dict],
    repeats: int,
    seed: int,
    slots: list[dict],
    locked_rounds: list[dict] | None = None,
) -> dict[str, Any]:
    """生成（或在有锁定轮次时续排）轮次表。

    返回 {"rounds": [...], "events": [...], "params": {...}, "uneven": bool}
    """
    locked_rounds = locked_rounds or []
    combos = build_combos(ivs)
    repeats = max(1, int(repeats or 1))
    rec = RngRecorder(seed)

    if not combos:
        return {"rounds": [], "events": rec.events,
                "params": {"repeats": repeats, "seed": seed, "combos": []},
                "uneven": False}

    combo_keys = [_combo_key(c) for c in combos]
    total = len(combos) * repeats
    uneven = False

    # 已锁定轮次占用的位置（seq 从 1 开始）
    locked_pos: dict[int, dict] = {}
    locked_count: dict[str, int] = {k: 0 for k in combo_keys}
    for r in locked_rounds:
        combo = r["combo"] if isinstance(r["combo"], dict) else json.loads(r["combo"])
        key = _combo_key(combo)
        if key in locked_count:
            locked_count[key] += 1
        locked_pos[int(r["seq"]) - 1] = combo

    if locked_pos:
        # 续排：每个组合剩余配额 = 目标重复数 - 已锁定次数；为 0/负数时不补
        pool: list[dict[str, str]] = []
        counts = {}
        for combo, key in zip(combos, combo_keys):
            remain = max(0, repeats - locked_count.get(key, 0))
            counts[key] = remain
            pool.extend([dict(combo) for _ in range(remain)])
        # 判断最终每个组合出现次数是否相等：锁定次数 + 剩余配额
        rec.shuffle(pool, "unlocked 剩余轮次")
        final_counts = {k: locked_count.get(k, 0) + counts[k] for k in combo_keys}
        uneven = len(set(final_counts.values())) > 1
        # 总长度仍保持 repeats*全组合；锁定位置超出时按实际最大位置展开
        positions = max(total, max(locked_pos) + 1)
        rows: list[dict | None] = [None] * positions
        for pos, combo in locked_pos.items():
            rows[pos] = {"combo": combo, "locked": True, "block_no": 0}
        fill = 0
        for i in range(positions):
            if rows[i] is None:
                rows[i] = {"combo": pool[fill] if fill < len(pool) else None,
                           "locked": False, "block_no": 0}
                fill += 1
        rows = [r for r in rows if r and r["combo"] is not None]
        for idx, r in enumerate(rows, 1):
            r["seq"] = idx
    else:
        # 全新排演：区组平衡随机
        rows = []
        for block in range(repeats):
            shuffled = rec.shuffle([dict(c) for c in combos], f"区组 {block + 1}")
            for combo in shuffled:
                rows.append({"combo": combo, "locked": False, "block_no": block + 1,
                             "seq": len(rows) + 1})

    # 时段分配：按容量循环（同一时段可排 capacity 轮，再轮到下一时段）
    slot_cycle: list[int] = []
    for s in sorted(slots, key=lambda x: x.get("ord", 0)):
        slot_cycle.extend([int(s["id"])] * max(1, int(s.get("capacity", 1))))
    for idx, r in enumerate(rows):
        r["slot_id"] = slot_cycle[idx] if slot_cycle and idx < len(slot_cycle) else None

    return {
        "rounds": rows,
        "events": rec.events,
        "uneven": uneven,
        "params": {"repeats": repeats, "seed": seed,
                   "combos": combo_keys, "locked": sorted(locked_pos)},
    }


def replay(events: list[dict], params: dict) -> dict:
    """用记录的 RNG 事件在相同输入上重放，核对每次随机选择是否一致。"""
    combos = [json.loads(k) for k in params.get("combos", [])]
    repeats = int(params.get("repeats", 1))
    seed = int(params.get("seed", 0))
    rng = random.Random(seed)

    ok = True
    details = []
    # 重放区组洗牌（无锁定情形）
    pool_template = [dict(c) for c in combos]
    for ev in events:
        if ev.get("type") != "shuffle":
            continue
        n = int(ev["n"])
        actual = list(ev.get("picks", []))
        expected = [rng.randrange(i + 1) for i in range(n - 1, 0, -1)]
        same = actual == expected
        ok = ok and same
        details.append({
            "label": ev.get("label"),
            "n": n,
            "match": same,
            # 重放得到的洗牌结果
            "result_order": None,
        })
        # 用记录的 picks 还原顺序，便于展示
        items = list(pool_template) if n == len(pool_template) else list(range(n))
        picks = actual
        k = 0
        for i in range(n - 1, 0, -1):
            if k < len(picks):
                j = picks[k]
                items[i], items[j] = items[j], items[i]
            k += 1
        details[-1]["result_order"] = [
            _combo_key(x) if isinstance(x, dict) else x for x in items
        ] if n == len(pool_template) else items

    # 重新生成一遍期望次序
    rec = RngRecorder(seed)
    for block in range(repeats):
        rec.shuffle([dict(c) for c in combos], f"区组 {block + 1}")
    return {"ok": ok, "seed": seed, "details": details,
            "replayed_events": rec.events == events if not params.get("locked") else None}

"""一键灌入一个可演示的探究：光照时间对绿豆发芽长度的影响。"""
import json
import urllib.request

BASE = "http://127.0.0.1:5000"


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


DESIGN = {
    "name": "光照时间对绿豆发芽长度的影响",
    "hypothesis": "如果每天给绿豆更多光照，那么它的芽会长得更长，因为光为生长提供能量。",
    "repeats": 3,
    "factors": [
        {"name": "光照时间", "kind": "independent", "unit": "",
         "levels": ["0h", "4h", "8h"]},
        {"name": "芽的长度", "kind": "dependent", "unit": "mm", "levels": []},
        {"name": "温度", "kind": "controlled", "unit": "", "levels": ["25℃"]},
        {"name": "每天水量", "kind": "controlled", "unit": "", "levels": ["20ml"]},
        {"name": "绿豆品种", "kind": "controlled", "unit": "", "levels": ["同一袋绿豆"]},
    ],
    "materials": [
        {"name": "绿豆", "per_round": 10, "stock": 100, "unit": "粒"},
        {"name": "培养皿", "per_round": 1, "stock": 9, "unit": "个"},
        {"name": "清水", "per_round": 20, "stock": 300, "unit": "ml"},
    ],
    "slots": [
        {"label": "周三下午 14:00", "capacity": 5},
        {"label": "周五下午 14:00", "capacity": 5},
    ],
}


def main():
    q = call("POST", "/api/inquiries", {"name": DESIGN["name"]})
    iid = q["inquiry"]["id"]
    call("PUT", f"/api/inquiries/{iid}", DESIGN)
    s = call("POST", f"/api/inquiries/{iid}/schedule", {})
    print(f"已创建探究 #{iid}，排出 {len(s['rounds'])} 轮。")
    print("打开 http://127.0.0.1:5000/ 即可体验。")


if __name__ == "__main__":
    main()

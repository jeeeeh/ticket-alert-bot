"""
NOL 티켓 예매 오픈 알림 (1시간마다, GitHub Actions, 무료)

settings.txt 에 공연 링크와 날짜를 넣어두면
- 그 날짜 회차가 새로 등록되고 오픈 시각이 정해지면 → 📢 오픈 예정 알림 (1번)
- 예매가 열리면 → 🎫 예매 가능 알림 + 좌석 등급별 잔여석 (1번)
알림은 대상마다 한 번씩만. 예매는 직접 하시면 돼요 (자동 예매 X).
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
REPO = os.environ.get("GITHUB_REPOSITORY", "jeeeeh/ticket-alert-bot")
EDIT_URL = f"https://github.com/{REPO}/edit/main/settings.txt"


def load_settings():
    """settings.txt → (켜짐 여부, [(공연번호, 날짜)])"""
    on, targets = True, []
    for line in (ROOT / "settings.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("상태"):
            on = "꺼" not in line
            continue
        pid = re.search(r"products/(\d+)", line) or re.match(r"(\d{6,})", line)
        date = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", line)
        if pid and date:
            y, m, d = date.groups()
            targets.append((pid.group(1), f"{y}-{int(m):02d}-{int(d):02d}"))
    return on, targets
STATE = ROOT / "data" / "state.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_IDS = [c.strip() for c in os.environ.get("CHAT_ID", "").split(",") if c.strip()]
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
DOW = "월화수목금토일"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
     "Accept": "application/json"}
BASE = "https://nol.yanolja.com/ticket/products"


def product_info(pid):
    """공연 페이지에서 공연명·goodsKey"""
    t = requests.get(f"{BASE}/{pid}", headers=H, timeout=20).text.replace('\\"', '"')
    name = re.search(r'"goodsName":"([^"]+)"', t)
    key = re.search(r'"goodsKey":"(\d+:\d+)"', t)
    if not key:
        raise RuntimeError("공연 정보를 찾지 못했어요 (페이지 구조가 바뀌었을 수 있어요)")
    return (name.group(1) if name else pid), key.group(1)


def schedules(goods_key, date):
    r = requests.get(f"{BASE}/api/schedules", headers={**H, "Referer": f"{BASE}/{goods_key.split(':')[0]}"},
                     params={"goodsKey": goods_key, "playStartDate": date, "playEndDate": date}, timeout=20)
    r.raise_for_status()
    return [s for s in r.json().get("content", []) if s.get("playDate") == date]


def remaining(goods_code, play_seq):
    try:
        r = requests.get(f"{BASE}/api/remaining-seats", headers=H,
                         params={"goodsCode": goods_code, "playSeq": play_seq}, timeout=20)
        r.raise_for_status()
        return r.json().get("remainSeat", [])
    except Exception:
        return None


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)


def kdate(d):
    dt = datetime.strptime(d, "%Y-%m-%d")
    return f"{dt.month}/{dt.day}({DOW[dt.weekday()]})"


def send(text):
    print(text)
    if not (BOT_TOKEN and CHAT_IDS) or os.environ.get("DRY_RUN") == "true":
        return
    for cid in CHAT_IDS:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", timeout=30,
                      json={"chat_id": cid, "text": text, "disable_web_page_preview": True})


FOOTER = ("\n\n━━━━━━━━━━━━━━\n"
          "⏸ 추적 그만하기 / 잠시 중단\n"
          "→ 아래 링크에서 '상태: 켜짐'을 '꺼짐'으로\n"
          f"{EDIT_URL}\n\n"
          "✏️ 공연·날짜 등 조건 바꾸기\n"
          "→ 아래 링크에서 공연 링크·날짜 줄을 수정\n"
          f"{EDIT_URL}")


def weekly_notice(on, targets, state):
    lines = ["📌 [NOL 예매 알림 봇] 주간 안내", ""]
    if not on:
        lines += ["지금은 ⏸ 중단 상태예요. (확인하지 않는 중)", "다시 켜려면 '상태: 꺼짐'을 '켜짐'으로 바꿔주세요."]
    elif not targets:
        lines += ["추적 중인 공연이 없어요."]
    else:
        lines.append("지금 1시간마다 확인 중인 공연:")
        for pid, date in targets:
            st = state.get(f"{pid}|{date}", {})
            name = st.get("name")
            if not name:
                try:
                    name = product_info(pid)[0]
                except Exception:
                    name = f"공연 {pid}"
            if st.get("open_sent"):
                stat = "✅ 예매 오픈 알림 보냄 (더 볼 필요 없으면 중단하세요)"
            elif st.get("notice_sent"):
                stat = "🕑 오픈 일정 나옴, 오픈 대기 중"
            else:
                stat = "⌛ 아직 회차 등록 전"
            lines += [f"· {name}", f"   📅 {kdate(date)} · {stat}"]
    send("\n".join(lines) + FOOTER)


def main():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    on, targets = load_settings()
    if os.environ.get("WEEKLY") == "true":
        weekly_notice(on, targets, state)
        return
    if not on:
        print("⏸ 꺼짐 상태라 확인 안 함")
        return
    for pid, date in targets:
        key = f"{pid}|{date}"
        st = state.setdefault(key, {})
        if st.get("open_sent"):
            print(f"{key}: 이미 예매 가능 알림 보냄")
            continue
        try:
            name, goods_key = product_info(pid)
            shows = schedules(goods_key, date)
        except Exception as e:
            print(f"! {key}: {e}")
            st["errors"] = st.get("errors", 0) + 1
            if st["errors"] == 6:  # 6시간 연속 실패하면 한 번 알려줌
                send(f"⚠️ [NOL 예매 알림 봇] {date} 확인이 계속 실패하고 있어요.\n{BASE}/{pid}" + FOOTER)
            continue
        st["errors"] = 0
        st["name"] = name
        link = f"{BASE}/{pid}"
        if not shows:
            print(f"{name} {date}: 아직 회차 없음")
            continue
        opened = [s for s in shows if ts(s["saleOpenTime"]) <= NOW < ts(s["saleCloseTime"])]
        if opened:
            lines = [f"🎫 [NOL 예매 알림 봇] 예매 가능해졌어요!", "", f"{name}", f"📅 {kdate(date)}", ""]
            for s in opened:
                seats = remaining(pid, s["playSeq"])
                if seats:
                    total = sum(x.get("remainCnt", 0) for x in seats)
                    seat_txt = " · ".join(f"{x['seatGradeName']} {x['remainCnt']}석" for x in seats) if total else "매진"
                else:
                    seat_txt = "잔여석 확인 불가"
                lines.append(f"⏰ {s['playTime']} — {seat_txt}")
            lines += ["", f"👉 {link}"]
            send("\n".join(lines) + FOOTER)
            st["open_sent"] = NOW.isoformat()
        elif not st.get("notice_sent"):
            first = min(shows, key=lambda s: s["saleOpenTime"])
            o = ts(first["saleOpenTime"])
            times = ", ".join(s["playTime"] for s in shows)
            send("\n".join([
                "📢 [NOL 예매 알림 봇] 예매 오픈 일정이 나왔어요!", "", f"{name}", f"📅 {kdate(date)} {times}",
                f"🕑 오픈: {o.month}/{o.day}({DOW[o.weekday()]}) {o:%H:%M}", "", f"👉 {link}",
                "", "열리면 다시 알려드릴게요."]) + FOOTER)
            st["notice_sent"] = NOW.isoformat()
        else:
            print(f"{name} {date}: 오픈 대기 중")
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

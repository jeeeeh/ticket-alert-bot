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


# ───────── 텔레그램 대화로 설정 바꾸기 ─────────
def tg(method, **params):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=params, timeout=30)
    return r.json()


def write_settings(on, targets):
    head = [l for l in (ROOT / "settings.txt").read_text(encoding="utf-8").splitlines()
            if l.startswith("#") and "공연링크" not in l and "날짜는" not in l and "추적할 공연" not in l
            and "켜기/끄기" not in l]
    body = ["", "# ① 알림 켜기/끄기 → 켜짐 또는 꺼짐", f"상태: {'켜짐' if on else '꺼짐'}", "",
            "# ② 추적할 공연 (한 줄에 하나씩: 공연링크 날짜)",
            "#    날짜는 2026-12-05 처럼 쓰기. 여러 줄 넣어도 돼요."]
    body += [f"{BASE}/{pid} {date}" for pid, date in targets]
    (ROOT / "settings.txt").write_text("\n".join(head + body) + "\n", encoding="utf-8")


def parse_dates(text):
    """'2026-12-12', '12/12', '12월 12일', '12.12' → ['2026-12-12', ...] (연도 없으면 다가오는 날짜)"""
    out = []
    for y, m, d in re.findall(r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", text):
        out.append(f"{y}-{int(m):02d}-{int(d):02d}")
    if not out:
        for m, d in re.findall(r"(?<!\d)(\d{1,2})\s*(?:월\s*|[/.])\s*(\d{1,2})\s*일?", text):
            m, d = int(m), int(d)
            if 1 <= m <= 12 and 1 <= d <= 31:
                y = NOW.year if (m, d) >= (NOW.month, NOW.day) else NOW.year + 1
                out.append(f"{y}-{m:02d}-{d:02d}")
    return out


def describe(on, targets, state):
    lines = [f"상태: {'▶️ 켜짐 (1시간마다 확인)' if on else '⏸ 꺼짐'}"]
    for pid, date in targets:
        name = state.get(f"{pid}|{date}", {}).get("name") or f"공연 {pid}"
        lines.append(f"· {name} — {kdate(date)}")
    if not targets:
        lines.append("· 추적 중인 공연 없음")
    return "\n".join(lines)


def handle_commands(state):
    """이 봇 대화방에 보낸 메시지를 읽어 설정을 바꾸고 답장. 바뀌면 True"""
    if not BOT_TOKEN:
        return False
    j = tg("getUpdates", offset=state.get("_tg_offset", 0), timeout=0, allowed_updates=["message"])
    if not j.get("ok"):
        print("! getUpdates:", j.get("description"))
        return False
    changed = False
    for up in j.get("result", []):
        state["_tg_offset"] = up["update_id"] + 1
        msg = up.get("message") or {}
        cid, text = str(msg.get("chat", {}).get("id")), (msg.get("text") or "").strip()
        if not text or (CHAT_IDS and cid not in CHAT_IDS):
            continue
        text = re.sub(r"@\w+", "", text).strip()
        on, targets = load_settings()
        pids = re.findall(r"products/(\d+)", text)
        dates = parse_dates(re.sub(r"https?://\S+", " ", text))
        reply = None
        if re.search(r"^/?(help|도움|사용법)|^/start", text):
            reply = None  # 아래 도움말
        elif re.search(r"중단|멈춰|그만|꺼|정지|stop", text):
            if on:
                write_settings(False, targets); changed = True
            reply = "⏸ 알림을 중단했어요. 다시 켜려면 '다시 켜줘'라고 보내주세요."
        elif re.search(r"재개|다시|켜|시작|resume", text) and not pids and not dates:
            if not on:
                write_settings(True, targets); changed = True
            reply = "▶️ 다시 1시간마다 확인할게요."
        elif re.search(r"삭제|빼|지워|제거", text) and (pids or dates):
            new = [(p, d) for p, d in targets
                   if not ((not pids or p in pids) and (not dates or d in dates))]
            if new != targets:
                write_settings(on, new); changed = True
                reply = "🗑 뺐어요."
            else:
                reply = "목록에서 해당 공연·날짜를 찾지 못했어요."
        elif pids and dates:
            new = [(p, d) for p in pids for d in dates]
            if re.search(r"추가|도|더", text):
                new = targets + [x for x in new if x not in targets]
                reply = "➕ 추가했어요."
            else:
                reply = "✏️ 바꿨어요."
            write_settings(True, new); changed = True
        elif dates and not pids:
            if len(targets) == 1:
                new = [(targets[0][0], d) for d in dates]
                write_settings(True, new); changed = True
                reply = "✏️ 날짜를 바꿨어요."
            elif not targets:
                reply = "공연 링크도 같이 보내주세요."
            else:
                reply = "공연이 여러 개라 어떤 공연인지 모르겠어요. 공연 링크와 날짜를 같이 보내주세요."
        elif pids and not dates:
            reply = "날짜도 같이 보내주세요. 예) 링크 12월 12일"
        elif re.search(r"목록|상태|뭐|확인", text):
            reply = ""
        if reply is None:
            reply = ("이렇게 보내주시면 바로 반영돼요 (10분 안에 답장):\n"
                     "· 중단 / 다시 켜줘\n· 12월 12일로 바꿔줘\n· (공연 링크) 12월 12일\n"
                     "· (공연 링크) 12월 12일 추가\n· 12월 5일 삭제\n· 목록")
        on2, targets2 = load_settings()
        for p, d in targets2:  # 새 공연 이름 미리 가져오기
            k = f"{p}|{d}"
            if not state.get(k, {}).get("name"):
                try:
                    state.setdefault(k, {})["name"] = product_info(p)[0]
                except Exception:
                    pass
        tg("sendMessage", chat_id=cid, text=(reply + "\n\n" if reply else "") + "📋 지금 설정\n" + describe(on2, targets2, state),
           disable_web_page_preview=True)
    return changed


FOOTER = ("\n\n━━━━━━━━━━━━━━\n"
          "💬 이 대화방에 이렇게 보내면 바로 바뀌어요\n"
          "⏸ 그만 보기: \"중단\"\n"
          "✏️ 날짜 변경: \"12월 12일로 바꿔줘\"\n"
          "✏️ 공연 변경: \"(공연 링크) 12월 12일\"\n"
          "➕ 추가: \"(공연 링크) 12월 12일 추가\"")


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
                stat = "✅ 예매 오픈 알림 보냄 (더 볼 필요 없으면 \"중단\" 보내주세요)"
            elif st.get("notice_sent"):
                stat = "🕑 오픈 일정 나옴, 오픈 대기 중"
            else:
                stat = "⌛ 아직 회차 등록 전"
            lines += [f"· {name}", f"   📅 {kdate(date)} · {stat}"]
    send("\n".join(lines) + FOOTER)


def main():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if handle_commands(state):
        print("설정 변경됨")
    save_state = lambda: (STATE.parent.mkdir(exist_ok=True),
                          STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"))
    on, targets = load_settings()
    if os.environ.get("WEEKLY") == "true":
        weekly_notice(on, targets, state)
        return save_state()
    last = state.get("_last_check")
    if os.environ.get("FORCE") != "true" and last and NOW - datetime.fromisoformat(last) < timedelta(minutes=55):
        print("이번엔 메시지만 확인 (티켓 확인은 1시간마다)")
        return save_state()
    state["_last_check"] = NOW.isoformat()
    if not on:
        print("⏸ 꺼짐 상태라 확인 안 함")
        return save_state()
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

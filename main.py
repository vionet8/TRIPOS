import json
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="TRIPOS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# areas_db をロード
AREAS_DB = json.loads((Path(__file__).parent / "data" / "areas_db.json").read_text(encoding="utf-8"))

# 全エリアをフラットリストに変換
def get_all_areas():
    areas = []
    for region in AREAS_DB["regions"]:
        for area in region["areas"]:
            area["region"] = region["label"]
            areas.append(area)
    return areas


class RecommendRequest(BaseModel):
    origin: str           # 出発地（例：宇都宮）
    destination: str = ""  # 目的地（帰省モードのみ必須）
    destination2: str = "" # 2つ目の目的地（妻実家など）
    direction: str = ""   # 方面の希望（観光モード・任意）
    purpose: str          # 旅の目的（観光/グルメ/温泉/レジャー/帰省）
    travel_date: str      # 出発日（例：2026-08-02）
    adults: int = 2
    children: str = ""    # 子供の年齢（例：3歳・6歳）
    nights: int = 1
    budget: str = "mid"   # budget/mid/premium/luxury
    mode: str = "normal"       # normal / kisei（帰省・長距離モード）
    group_type: str = "family" # family / couple / friends / solo
    weight_family: int = 3     # 子連れ重視度 1-5
    weight_cost: int = 3       # コスパ重視度 1-5
    weight_onsen: int = 3      # 温泉重視度 1-5
    round_trip: bool = False   # 往復提案（帰省モードのみ）
    fuel_efficiency: float = 15.0  # 燃費 km/L
    has_etc: bool = True           # ETC割引あり


@app.get("/")
async def index():
    html = (Path(__file__).parent / "static" / "app.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/lp")
async def lp_page():
    html = (Path(__file__).parent / "lp" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.post("/api/recommend")
async def recommend(req: RecommendRequest):
    import traceback
    from fastapi import HTTPException
    try:
        return await _recommend_inner(req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"[UNHANDLED] {type(e).__name__}: {str(e)}\n{traceback.format_exc()[-800:]}")

async def _recommend_inner(req: RecommendRequest):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    all_areas = get_all_areas()

    # エリアデータを簡潔な形式でプロンプトに渡す
    areas_summary = []
    for a in all_areas:
        child_score = 0
        fs = a.get("family_score", {})
        if fs:
            child_score = max(fs.values())
        areas_summary.append({
            "id": a["id"],
            "name": a["name"],
            "prefecture": a["prefecture"],
            "region": a["region"],
            "type": a["type"],
            "price_range": a["price_range"],
            "hotel_price_avg": a.get("hotel_price_avg", ""),
            "family_score_max": child_score,
            "best_season": a.get("best_season", []),
            "tags": a.get("tags", []),
            "attractions": a.get("attractions", [])[:3],
            "food": a.get("food", [])[:3],
        })

    mode_note = ""
    if req.mode == "kisei":
        dest2_note = f"→さらに「{req.destination2}」も経由する（2か所目の帰省先）。" if req.destination2 else ""
        round_note = ""
        if req.round_trip:
            round_note = (
                "【往復モード】行き（出発地→目的地の中間）と帰り（目的地→出発地の中間）の両方を提案すること。"
                "帰りの提案(return_areas)は行きで漏れた良エリアを優先的に使い、なるべく別ルートを推奨すること。"
            )
        mode_note = f"※帰省・長距離モード：出発地〜目的地の中間地点として距離的に適切なエリアを提案。{dest2_note}中間地点での滞在目的は「{req.purpose}」に合うエリアを選ぶ。{round_note}"
    else:
        dest_note = f"方面の希望：{req.direction}" if req.direction else "方面の希望：特になし（AIが最適なエリアを自由に提案）"
        mode_note = f"※観光旅行モード：目的地は決まっていない。出発地から車で現実的に行ける範囲で、旅の目的・同行者に最もマッチするエリアをAIが提案すること。{dest_note}"

    group_labels = {"family":"子連れ家族", "couple":"カップル（大人2人）", "friends":"複数人グループ（子供なし）", "solo":"ひとり旅"}
    group_note = group_labels.get(req.group_type, req.group_type)
    family_note = ""
    if req.group_type != "family":
        family_note = "※子連れではないため、family_scoreは考慮不要。大人向けの観光・グルメ・温泉を重視すること。"

    weight_note = f"ユーザーの重視度（1〜5）: 子連れ適性={req.weight_family} / コスパ={req.weight_cost} / 温泉={req.weight_onsen}"

    etc_note = "ETC割引あり（約30〜50%引き）" if req.has_etc else "ETC割引なし"
    cost_note = f"【費用計算】燃費={req.fuel_efficiency}km/L、ガソリン単価=約175円/L、高速料金目安=約25円/km（{etc_note}）。距離を推定して往復のガソリン代・高速代を計算すること。"

    prompt = f"""あなたはTRIPOSというAI旅行コンシェルジュです。
家族の車旅行において、出発地から目的地への移動ルート上で最適な「宿泊エリア」を提案してください。

## 旅行条件
- 出発地：{req.origin}
- 目的地：{req.destination if req.destination else "未定（AIが提案）"}{f" → さらに{req.destination2}" if req.destination2 else ""}
- 旅の目的：{req.purpose}
- 出発日：{req.travel_date}
- 同行者：{group_note}
- 人数：大人{req.adults}人、子供 {req.children if req.children else "なし"}
- 泊数：{req.nights}泊
- 予算感：{req.budget}（budget=〜8000円/人、mid=8000〜15000円、premium=15000〜25000円）
- {weight_note}
{mode_note}
{family_note}
{cost_note if req.mode == "kisei" else ""}

## エリアデータベース（抜粋・ルート関連エリア優先）
{json.dumps(areas_summary[:60], ensure_ascii=False, indent=None)}

## 指示
1. 出発地→目的地のルートを地理的に考慮し、中継地として現実的なエリアを選ぶ
2. 旅の目的・子連れ条件・予算に合うエリアを優先する
3. 「人気No.1」ではなく「今のこの家族に最適な狙い目」を選ぶ
4. 必ず行きの提案を3エリア提案すること
5. 帰省モードかつ往復の場合、return_areasとして帰りの提案も3エリア提案すること（行きと異なるルート・エリアを推奨）
6. 帰省モードの場合、費用サマリー（距離・ガソリン代・高速代・宿泊代の合計）を必ず計算すること

## 回答形式（JSON形式で返すこと、他の文章は不要）
{{
  "areas": [
    {{
      "rank": 1,
      "id": "エリアID",
      "name": "エリア名",
      "prefecture": "都道府県",
      "score": 85,
      "score_breakdown": {{
        "popularity": 70,
        "satisfaction": 85,
        "family_fit": 90,
        "crowding_penalty": -20,
        "price_surge_penalty": -10
      }},
      "reason": "このエリアを推す理由（2〜3文、具体的に）",
      "highlight": "一言キャッチ",
      "price_range": "mid",
      "hotel_price_avg": "8000-15000",
      "tags": ["タグ1","タグ2"],
      "attractions": ["見どころ1","見どころ2"],
      "food": ["グルメ1","グルメ2"],
      "wishlist_match": false
    }}
  ],
  "return_areas": [],
  "route_comment": "ルート全体へのひとこと",
  "cost_summary": {{
    "estimated_distance_km": 500,
    "fuel_cost": 5800,
    "toll_cost": 8000,
    "hotel_cost_min": 16000,
    "hotel_cost_max": 30000,
    "total_min": 29800,
    "total_max": 43800,
    "note": "費用の補足コメント（ETC割引・往復の場合は往復合計など）"
  }}
}}"""

    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Claude API error: {str(e)}")

    raw = message.content[0].text.strip()

    # JSON部分を抽出
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"JSON parse error: {str(e)} / raw: {raw[:200]}")

    return result


# ─────────────────────────────────────────
# 先行登録API
# ─────────────────────────────────────────
class RegisterRequest(BaseModel):
    origin: str
    destination: str = ""
    children: str = ""
    email: str = ""
    comment: str = ""

@app.post("/api/register")
async def register(req: RegisterRequest):
    import storage as _storage
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    entries = _storage.load("tripos_registrations")
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "origin": req.origin,
        "destination": req.destination,
        "children": req.children,
        "email": req.email,
        "comment": req.comment,
        "registered_at": datetime.now(JST).isoformat(),
    })
    _storage.save("tripos_registrations", entries)
    return {"ok": True}


# ─────────────────────────────────────────
# 要件定義ページ
# ─────────────────────────────────────────
@app.get("/requirements")
async def requirements_page():
    html = (Path(__file__).parent / "static" / "requirements.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ─────────────────────────────────────────
# 管理ページ
# ─────────────────────────────────────────
def _check_admin(key: str):
    from fastapi import HTTPException
    secret = os.environ.get("ADMIN_KEY", "tripos-admin")
    if key != secret:
        raise HTTPException(status_code=403, detail="Forbidden")

@app.get("/admin")
async def admin_index(key: str = ""):
    _check_admin(key)
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>TRIPOS 管理</title>
    <style>
      body{{font-family:-apple-system,sans-serif;padding:32px 24px;background:#f0f4f8;color:#222;max-width:600px;margin:0 auto}}
      h1{{font-size:1.4rem;margin-bottom:4px;color:#1B3A6B}}
      .sub{{color:#888;font-size:.9rem;margin-bottom:32px}}
      .card{{background:#fff;border-radius:14px;box-shadow:0 2px 8px rgba(0,0,0,.08);padding:20px 24px;margin-bottom:12px;display:flex;align-items:center;gap:16px;text-decoration:none;color:inherit;transition:box-shadow .15s}}
      .card:hover{{box-shadow:0 4px 16px rgba(0,0,0,.13)}}
      .icon{{font-size:2rem;flex-shrink:0}}
      .card-title{{font-size:1rem;font-weight:700;margin-bottom:2px}}
      .card-desc{{font-size:.83rem;color:#888}}
      .arrow{{margin-left:auto;color:#ccc;font-size:1.3rem}}
    </style></head><body>
    <h1>🧭 TRIPOS 管理</h1>
    <p class="sub">管理者メニュー</p>
    <a class="card" href="/admin/registrations?key={key}">
      <div class="icon">📝</div><div>
        <div class="card-title">先行登録一覧</div>
        <div class="card-desc">LPからのリクエストカード・出発地・コメントの確認</div>
      </div><div class="arrow">›</div>
    </a>
    <a class="card" href="/admin/ambassadors?key={key}">
      <div class="icon">🌟</div><div>
        <div class="card-title">アンバサダー管理</div>
        <div class="card-desc">申請の承認・実績確認・紹介コード管理</div>
      </div><div class="arrow">›</div>
    </a>
    <a class="card" href="/admin/subscribers?key={key}">
      <div class="icon">💳</div><div>
        <div class="card-title">課金者管理</div>
        <div class="card-desc">有料プラン加入者・プラン・請求状況の確認</div>
      </div><div class="arrow">›</div>
    </a>
    <a class="card" href="/requirements" target="_blank">
      <div class="icon">📋</div><div>
        <div class="card-title">要件定義・仕様書</div>
        <div class="card-desc">機能一覧・ロードマップ・技術仕様の確認</div>
      </div><div class="arrow">›</div>
    </a>
    </body></html>"""
    return HTMLResponse(content=html)


@app.get("/admin/registrations")
async def admin_registrations(key: str = ""):
    _check_admin(key)
    import storage as _storage
    entries = _storage.load("tripos_registrations")
    if not isinstance(entries, list):
        entries = []
    rows = ""
    for e in reversed(entries):
        rows += f"""<tr>
          <td>{e.get('registered_at','')[:16]}</td>
          <td>{e.get('origin','—')}</td>
          <td>{e.get('destination','—') or '（観光モード）'}</td>
          <td>{e.get('children','—') or '—'}</td>
          <td>{e.get('email','—') or '—'}</td>
          <td style="max-width:220px;white-space:pre-wrap">{e.get('comment','—') or '—'}</td>
        </tr>"""
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>TRIPOS 先行登録一覧</title>
    <style>
      body{{font-family:-apple-system,sans-serif;padding:24px;background:#f0f4f8;color:#222}}
      h1{{font-size:1.3rem;margin-bottom:4px;color:#1B3A6B}}
      .count{{color:#888;font-size:.9rem;margin-bottom:20px}}
      .back{{display:inline-block;margin-bottom:16px;color:#1B3A6B;font-size:.9rem;text-decoration:none}}
      table{{border-collapse:collapse;width:100%;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
      th{{background:#1B3A6B;color:#fff;padding:10px 14px;font-size:.8rem;text-align:left;white-space:nowrap}}
      td{{padding:10px 14px;font-size:.85rem;border-bottom:1px solid #eee;vertical-align:top}}
      tr:last-child td{{border-bottom:none}}
      tr:hover td{{background:#f8faff}}
    </style></head><body>
    <a class="back" href="/admin?key={key}">← 管理メニューへ</a>
    <h1>📝 先行登録一覧</h1>
    <p class="count">合計 <strong>{len(entries)}</strong> 件</p>
    <table><thead><tr>
      <th>日時</th><th>出発地</th><th>目的地</th><th>子供年齢</th><th>メール</th><th>コメント</th>
    </tr></thead><tbody>{rows or '<tr><td colspan="6" style="text-align:center;color:#aaa;padding:32px">まだ登録がありません</td></tr>'}</tbody></table>
    </body></html>"""
    return HTMLResponse(content=html)


@app.get("/admin/ambassadors")
async def admin_ambassadors(key: str = ""):
    _check_admin(key)
    import storage as _storage
    entries = _storage.load("tripos_ambassadors")
    if not isinstance(entries, list):
        entries = []
    rows = ""
    for e in reversed(entries):
        status_color = {"pending":"#f5a623","active":"#2AB4A0","inactive":"#ccc"}.get(e.get('status','pending'),'#ccc')
        rows += f"""<tr>
          <td>{e.get('registered_at','')[:16]}</td>
          <td>{e.get('name','—')}</td>
          <td>{e.get('email','—')}</td>
          <td>{e.get('channel','—')}</td>
          <td>{e.get('followers','—')}</td>
          <td><span style="color:{status_color};font-weight:700">{e.get('status','pending')}</span></td>
          <td>{e.get('referral_code','—')}</td>
          <td>{e.get('referrals',0)}</td>
        </tr>"""
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>TRIPOS アンバサダー管理</title>
    <style>
      body{{font-family:-apple-system,sans-serif;padding:24px;background:#f0f4f8;color:#222}}
      h1{{font-size:1.3rem;margin-bottom:4px;color:#1B3A6B}}
      .count{{color:#888;font-size:.9rem;margin-bottom:20px}}
      .back{{display:inline-block;margin-bottom:16px;color:#1B3A6B;font-size:.9rem;text-decoration:none}}
      table{{border-collapse:collapse;width:100%;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
      th{{background:#1B3A6B;color:#fff;padding:10px 14px;font-size:.8rem;text-align:left;white-space:nowrap}}
      td{{padding:10px 14px;font-size:.85rem;border-bottom:1px solid #eee;vertical-align:top}}
      tr:last-child td{{border-bottom:none}}
      tr:hover td{{background:#f8faff}}
    </style></head><body>
    <a class="back" href="/admin?key={key}">← 管理メニューへ</a>
    <h1>🌟 アンバサダー管理</h1>
    <p class="count">合計 <strong>{len(entries)}</strong> 名</p>
    <table><thead><tr>
      <th>申請日</th><th>名前</th><th>メール</th><th>発信チャネル</th><th>フォロワー数</th><th>ステータス</th><th>紹介コード</th><th>紹介数</th>
    </tr></thead><tbody>{rows or '<tr><td colspan="8" style="text-align:center;color:#aaa;padding:32px">まだ申請がありません</td></tr>'}</tbody></table>
    </body></html>"""
    return HTMLResponse(content=html)


@app.get("/admin/subscribers")
async def admin_subscribers(key: str = ""):
    _check_admin(key)
    import storage as _storage
    entries = _storage.load("tripos_subscribers")
    if not isinstance(entries, list):
        entries = []
    rows = ""
    for e in reversed(entries):
        status_color = {"active":"#2AB4A0","cancelled":"#e74c3c","trial":"#f5a623"}.get(e.get('status','trial'),'#ccc')
        rows += f"""<tr>
          <td>{e.get('started_at','')[:16]}</td>
          <td>{e.get('name','—')}</td>
          <td>{e.get('email','—')}</td>
          <td>{e.get('plan','—')}</td>
          <td><span style="color:{status_color};font-weight:700">{e.get('status','—')}</span></td>
          <td>{e.get('amount','—')}</td>
          <td>{e.get('next_billing','—')}</td>
          <td>{e.get('referral_code','—') or '—'}</td>
        </tr>"""
    total_mrr = sum(e.get('amount', 0) for e in entries if e.get('status') == 'active')
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>TRIPOS 課金者管理</title>
    <style>
      body{{font-family:-apple-system,sans-serif;padding:24px;background:#f0f4f8;color:#222}}
      h1{{font-size:1.3rem;margin-bottom:4px;color:#1B3A6B}}
      .meta{{color:#888;font-size:.9rem;margin-bottom:20px}}
      .back{{display:inline-block;margin-bottom:16px;color:#1B3A6B;font-size:.9rem;text-decoration:none}}
      .stat{{display:inline-block;background:#fff;border-radius:10px;padding:12px 20px;margin:0 8px 16px 0;box-shadow:0 2px 8px rgba(0,0,0,.07)}}
      .stat-val{{font-size:1.6rem;font-weight:900;color:#1B3A6B}}
      .stat-label{{font-size:.75rem;color:#888}}
      table{{border-collapse:collapse;width:100%;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
      th{{background:#1B3A6B;color:#fff;padding:10px 14px;font-size:.8rem;text-align:left;white-space:nowrap}}
      td{{padding:10px 14px;font-size:.85rem;border-bottom:1px solid #eee;vertical-align:top}}
      tr:last-child td{{border-bottom:none}}
      tr:hover td{{background:#f8faff}}
    </style></head><body>
    <a class="back" href="/admin?key={key}">← 管理メニューへ</a>
    <h1>💳 課金者管理</h1>
    <div>
      <div class="stat"><div class="stat-val">{len(entries)}</div><div class="stat-label">合計加入者</div></div>
      <div class="stat"><div class="stat-val">¥{total_mrr:,}</div><div class="stat-label">MRR（月次売上）</div></div>
    </div>
    <table><thead><tr>
      <th>開始日</th><th>名前</th><th>メール</th><th>プラン</th><th>状態</th><th>金額/月</th><th>次回請求</th><th>紹介コード</th>
    </tr></thead><tbody>{rows or '<tr><td colspan="8" style="text-align:center;color:#aaa;padding:32px">まだ課金者がいません</td></tr>'}</tbody></table>
    </body></html>"""
    return HTMLResponse(content=html)


# ─────────────────────────────────────────
# ヒアリングチャットAPI
# ─────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str   # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]

@app.post("/api/chat")
async def chat(req: ChatRequest):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = """あなたはTRIPOSのAI旅行コンシェルジュ「チャッピー」です。
家族旅行・帰省・長距離移動の計画を立てるお手伝いをします。

ユーザーと自然な会話でヒアリングし、以下の情報を収集してください：
1. 旅のタイプ（観光旅行 / 帰省・長距離移動）
2. 出発地（例：宇都宮、大阪）
3. 目的地（帰省の場合は必須。観光の場合はAIが提案）
4. 方面の希望（観光の場合・任意）
5. 旅の目的（観光 / グルメ / 温泉・ホテル / レジャー）
6. 出発予定日
7. 泊数（1〜10泊）
8. 同行者（子連れ家族 / カップル / 複数人グループ / ひとり旅）
9. 大人人数・子供年齢（子連れの場合）
10. 予算感（節約 / 標準 / プレミアム / 贅沢）
11. 帰省の場合：往復提案が必要か、燃費・ETCの有無

会話のルール：
- 一度に聞く質問は1〜2つまで。自然な会話にする
- すでに答えた情報は再度聞かない
- フレンドリーで温かいトーンで話す（タメ口ではなくです・ます調）
- 十分な情報が集まったら（最低限：出発地・旅のタイプ・目的・日程）、最後に以下のJSON形式で情報をまとめて返す（他の文章は不要）

十分な情報が集まったと判断したら、必ず以下の形式で返答する：
[COLLECTED]
{
  "mode": "normal または kisei",
  "origin": "出発地",
  "destination": "目的地（帰省の場合）",
  "destination2": "2つ目の目的地（あれば）",
  "direction": "方面の希望（あれば）",
  "purpose": "観光 または グルメ または 温泉・ホテル または レジャー",
  "travel_date": "YYYY-MM-DD",
  "nights": 泊数の整数,
  "adults": 大人人数,
  "children": "子供の年齢（例：3歳・6歳）",
  "budget": "budget または mid または premium または luxury",
  "group_type": "family または couple または friends または solo",
  "round_trip": trueまたはfalse,
  "fuel_efficiency": 燃費数値,
  "has_etc": trueまたはfalse,
  "summary": "収集した情報の日本語サマリー（1文）"
}
[/COLLECTED]"""

    messages_for_api = [{"role": m.role, "content": m.content} for m in req.messages]

    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            system=system_prompt,
            messages=messages_for_api
        )
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Claude API error: {str(e)}")

    reply = message.content[0].text.strip()

    # [COLLECTED]ブロックが含まれているか確認
    collected_data = None
    if "[COLLECTED]" in reply and "[/COLLECTED]" in reply:
        try:
            json_start = reply.index("[COLLECTED]") + len("[COLLECTED]")
            json_end = reply.index("[/COLLECTED]")
            json_str = reply[json_start:json_end].strip()
            collected_data = json.loads(json_str)
            # メッセージ部分はサマリーのみ
            reply = f"✅ ヒアリング完了！\n\n{collected_data.get('summary', '条件が整いました。')}\n\nこの内容で宿泊エリアを探しますね。「提案を見る」ボタンを押してください！"
        except Exception:
            pass

    return {"reply": reply, "collected": collected_data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

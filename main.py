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
        if not isinstance(region.get("areas"), list):
            continue
        for area in region["areas"]:
            area = dict(area)
            area["region"] = region["label"]
            area["region_id"] = region["id"]
            areas.append(area)
    return areas


# ── サブリージョン定義（ルート順）──────────────────────
# 日本を10のサブリージョンに分割し、地理的な順序を定義
SUBREGIIONS_ORDER = [
    "hokkaido",   # 北海道
    "tohoku",     # 東北
    "kanto",      # 関東
    "koshinetsu", # 甲信越
    "tokai",      # 東海
    "kinki",      # 関西・近畿
    "chugoku",    # 中国
    "shikoku",    # 四国
    "kyushu",     # 九州
    "okinawa",    # 沖縄
]

# 都市・県名 → サブリージョンID
PLACE_TO_SUBREGION = {
    # 北海道
    "hokkaido": ["北海道","札幌","函館","旭川","帯広","釧路","小樽","登別","洞爺","ニセコ","富良野","知床"],
    # 東北
    "tohoku": ["青森","岩手","宮城","秋田","山形","福島","仙台","盛岡","青森","秋田","山形","福島","郡山","いわき"],
    # 関東
    "kanto": ["東京","神奈川","埼玉","千葉","茨城","栃木","群馬","宇都宮","横浜","川崎","さいたま","水戸","前橋","高崎","那須","日光","鎌倉","箱根"],
    # 甲信越
    "koshinetsu": ["山梨","長野","新潟","甲府","松本","長野","上田","軽井沢","諏訪","清里","白馬","妙高","越後湯沢","湯沢"],
    # 東海
    "tokai": ["静岡","愛知","岐阜","三重","浜松","静岡","名古屋","岐阜","津","伊勢","下呂","飛騨","高山","熱海","伊豆"],
    # 関西・近畿
    "kinki": ["大阪","京都","兵庫","奈良","滋賀","和歌山","神戸","奈良","大津","和歌山","白浜","有馬","城崎","天橋立"],
    # 中国
    "chugoku": ["鳥取","島根","岡山","広島","山口","鳥取","松江","岡山","広島","下関","宮島"],
    # 四国
    "shikoku": ["徳島","香川","愛媛","高知","徳島","高松","松山","高知","道後"],
    # 九州
    "kyushu": ["福岡","佐賀","長崎","熊本","大分","宮崎","鹿児島","博多","北九州","長崎","熊本","別府","由布院","宮崎","鹿児島"],
    # 沖縄
    "okinawa": ["沖縄","那覇","石垣","宮古","沖縄本島"],
}

def detect_subregion(place: str) -> str | None:
    """地名からサブリージョンIDを返す"""
    for sub_id, keywords in PLACE_TO_SUBREGION.items():
        if any(kw in place for kw in keywords):
            return sub_id
    return None

# 都道府県・エリア名 → サブリージョンIDのマッピング（areas_dbのprefectureから判定）
PREFECTURE_TO_SUBREGION = {
    "北海道": "hokkaido",
    "青森県": "tohoku", "岩手県": "tohoku", "宮城県": "tohoku",
    "秋田県": "tohoku", "山形県": "tohoku", "福島県": "tohoku",
    "東京都": "kanto", "神奈川県": "kanto", "埼玉県": "kanto",
    "千葉県": "kanto", "茨城県": "kanto", "栃木県": "kanto", "群馬県": "kanto",
    "山梨県": "koshinetsu", "長野県": "koshinetsu", "新潟県": "koshinetsu",
    "静岡県": "tokai", "愛知県": "tokai", "岐阜県": "tokai", "三重県": "tokai",
    "大阪府": "kinki", "京都府": "kinki", "兵庫県": "kinki",
    "奈良県": "kinki", "滋賀県": "kinki", "和歌山県": "kinki",
    "鳥取県": "chugoku", "島根県": "chugoku", "岡山県": "chugoku",
    "広島県": "chugoku", "山口県": "chugoku",
    "徳島県": "shikoku", "香川県": "shikoku", "愛媛県": "shikoku", "高知県": "shikoku",
    "福岡県": "kyushu", "佐賀県": "kyushu", "長崎県": "kyushu",
    "熊本県": "kyushu", "大分県": "kyushu", "宮崎県": "kyushu", "鹿児島県": "kyushu",
    "沖縄県": "okinawa",
}

def get_area_subregion(area: dict) -> str:
    """エリアの都道府県からサブリージョンIDを返す"""
    pref = area.get("prefecture", "")
    # 「県」「府」「都」「道」を付けて検索
    for suffix in ["", "県", "府", "都", "道"]:
        if pref + suffix in PREFECTURE_TO_SUBREGION:
            return PREFECTURE_TO_SUBREGION[pref + suffix]
    # 前方一致で検索
    for k, v in PREFECTURE_TO_SUBREGION.items():
        if k.startswith(pref) or pref.startswith(k[:2]):
            return v
    return "kanto"  # デフォルト


def get_intermediate_subregions(origin: str, destination: str, mode: str = "normal") -> list[str]:
    """
    出発地〜目的地の中間サブリージョンを返す。
    帰省モード: 出発地・目的地を除いた中間のみ
    観光モード: 出発地周辺 + 少し遠め（目的地なし）
    """
    origin_sub = detect_subregion(origin) or "kanto"

    if mode == "kisei" and destination:
        dest_sub = detect_subregion(destination) or "kinki"
        o_idx = SUBREGIIONS_ORDER.index(origin_sub) if origin_sub in SUBREGIIONS_ORDER else 2
        d_idx = SUBREGIIONS_ORDER.index(dest_sub) if dest_sub in SUBREGIIONS_ORDER else 5

        if o_idx == d_idx:
            # 同じリージョン内は隣接も含める
            start, end = max(0, o_idx - 1), min(len(SUBREGIIONS_ORDER) - 1, o_idx + 1)
        else:
            # 中間のみ（出発地と目的地は除外）
            lo, hi = min(o_idx, d_idx), max(o_idx, d_idx)
            start, end = lo + 1, hi - 1  # 両端を除外

        if start > end:
            # 中間がない場合（隣接）は両方含める
            start, end = min(o_idx, d_idx), max(o_idx, d_idx)

        return SUBREGIIONS_ORDER[start:end + 1]
    else:
        # 観光モード：出発地から車で2〜3時間圏内（出発地±2サブリージョン）
        o_idx = SUBREGIIONS_ORDER.index(origin_sub) if origin_sub in SUBREGIIONS_ORDER else 2
        start = max(0, o_idx - 1)
        end = min(len(SUBREGIIONS_ORDER) - 1, o_idx + 2)
        return SUBREGIIONS_ORDER[start:end + 1]


def get_filtered_areas(origin: str, destination: str, mode: str = "normal", max_areas: int = 20) -> list:
    """ルートの中間エリアに絞り込んで返す"""
    target_subs = get_intermediate_subregions(origin, destination, mode)
    all_areas = get_all_areas()

    # エリアにサブリージョンを付与して絞り込み
    filtered = []
    for a in all_areas:
        sub = get_area_subregion(a)
        if sub in target_subs:
            a = dict(a)
            a["subregion"] = sub
            filtered.append(a)

    # 足りない場合はリージョン拡張
    if len(filtered) < 8:
        filtered = all_areas

    return filtered[:max_areas]


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


@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.head("/ping")
async def ping_head():
    return {}

@app.get("/api/debug")
async def debug():
    """起動診断エンドポイント"""
    import traceback
    result = {}
    # 環境変数チェック
    result["ANTHROPIC_API_KEY"] = "SET" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING"
    result["UPSTASH_REDIS_REST_URL"] = "SET" if os.environ.get("UPSTASH_REDIS_REST_URL") else "MISSING"
    # areas_db チェック
    try:
        areas = get_all_areas()
        result["areas_db"] = f"OK ({len(areas)} areas)"
    except Exception as e:
        result["areas_db"] = f"ERROR: {e}"
    # 利用可能モデル一覧
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        result["available_models"] = model_ids
    except Exception as e:
        result["available_models"] = f"ERROR: {str(e)}"
    # Claude API 簡易テスト（haiku で確認）
    test_models = ["claude-haiku-4-5", "claude-3-5-haiku-20241022", "claude-3-haiku-20240307"]
    for m in test_models:
        try:
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            msg = client.messages.create(model=m, max_tokens=10, messages=[{"role":"user","content":"hi"}])
            result["claude_api"] = f"OK with {m}: {msg.content[0].text[:20]}"
            result["working_model"] = m
            break
        except Exception as e:
            result[f"claude_{m}"] = f"{type(e).__name__}: {str(e)[:80]}"
    return result

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

    # 出発地・目的地でルート中間エリアに絞り込む
    dest_for_filter = req.destination if req.destination else req.direction
    route_areas = get_filtered_areas(req.origin, dest_for_filter, mode=req.mode, max_areas=20)

    # エリアデータを簡潔な形式でプロンプトに渡す
    areas_summary = []
    for a in route_areas:
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
{json.dumps(areas_summary, ensure_ascii=False, indent=None)}

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
            model="claude-sonnet-4-6",
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
# ユーザー認証（メールアドレスでID発行）
# ─────────────────────────────────────────
import hashlib
import uuid as _uuid

class LoginRequest(BaseModel):
    email: str

@app.post("/api/user/login")
async def user_login(req: LoginRequest):
    """メールアドレスでユーザーIDを発行/取得する（パスワード不要）"""
    import storage as _storage
    email = req.email.strip().lower()
    if not email or "@" not in email:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="メールアドレスが無効です")

    # メールアドレスをキーにユーザーIDを検索/作成
    key = f"tripos_user_email_{hashlib.sha256(email.encode()).hexdigest()[:16]}"
    existing = _storage.load(key)
    if existing and isinstance(existing, dict):
        user_id = existing["user_id"]
    else:
        user_id = str(_uuid.uuid4())[:12]
        _storage.save(key, {"user_id": user_id, "email": email})
        # ユーザーデータ初期化
        _storage.save(f"tripos_user_{user_id}", {"wishlist": [], "history": []})

    return {"user_id": user_id, "email": email}

@app.get("/api/user/{user_id}/data")
async def get_user_data(user_id: str):
    """ユーザーデータ取得（wishlist・history）"""
    import storage as _storage
    data = _storage.load(f"tripos_user_{user_id}")
    if not data or not isinstance(data, dict):
        return {"wishlist": [], "history": []}
    return data

@app.post("/api/user/{user_id}/wishlist")
async def save_wishlist(user_id: str, body: dict):
    """wishlistを保存"""
    import storage as _storage
    data = _storage.load(f"tripos_user_{user_id}")
    if not data or not isinstance(data, dict):
        data = {"wishlist": [], "history": []}
    data["wishlist"] = body.get("wishlist", [])
    _storage.save(f"tripos_user_{user_id}", data)
    return {"ok": True}

@app.post("/api/user/{user_id}/history")
async def save_history(user_id: str, body: dict):
    """旅行履歴を追加"""
    import storage as _storage
    data = _storage.load(f"tripos_user_{user_id}")
    if not data or not isinstance(data, dict):
        data = {"wishlist": [], "history": []}
    history = data.get("history", [])
    history.insert(0, body.get("entry", {}))
    data["history"] = history[:20]  # 最大20件
    _storage.save(f"tripos_user_{user_id}", data)
    return {"ok": True}


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
            model="claude-sonnet-4-6",
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

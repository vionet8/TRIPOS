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
    destination: str      # 目的地（例：大阪）
    purpose: str          # 旅の目的（観光/グルメ/温泉/レジャー）
    travel_date: str      # 出発日（例：2026-08-02）
    adults: int = 2
    children: str = ""    # 子供の年齢（例：3歳・6歳）
    nights: int = 1
    budget: str = "mid"   # budget/mid/premium/luxury


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

    prompt = f"""あなたはTRIPOSというAI旅行コンシェルジュです。
家族の車旅行において、出発地から目的地への移動ルート上で最適な「宿泊エリア」を提案してください。

## 旅行条件
- 出発地：{req.origin}
- 目的地：{req.destination}
- 旅の目的：{req.purpose}
- 出発日：{req.travel_date}
- 人数：大人{req.adults}人、子供 {req.children if req.children else "なし"}
- 泊数：{req.nights}泊
- 予算感：{req.budget}（budget=〜8000円/人、mid=8000〜15000円、premium=15000〜25000円）

## エリアデータベース（125エリア）
{json.dumps(areas_summary, ensure_ascii=False, indent=None)}

## 指示
1. 出発地→目的地のルートを地理的に考慮し、中継地として現実的なエリアを選ぶ
2. 旅の目的・子連れ条件・予算に合うエリアを優先する
3. 「人気No.1」ではなく「今のこの家族に最適な狙い目」を選ぶ
4. 必ず3エリアを提案すること

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
      "highlight": "一言キャッチ（例：うなぎの産地、今が穴場）",
      "price_range": "mid",
      "hotel_price_avg": "8000-15000",
      "tags": ["タグ1","タグ2"],
      "attractions": ["見どころ1","見どころ2"],
      "food": ["グルメ1","グルメ2"],
      "wishlist_match": false
    }}
  ],
  "route_comment": "ルート全体へのひとこと（渋滞・距離感など）"
}}"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # JSON部分を抽出
    start = raw.find("{")
    end = raw.rfind("}") + 1
    result = json.loads(raw[start:end])

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

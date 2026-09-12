# PitLine — 執行計劃

求職 demo 用技術規劃書：把 VoiceAgent（水電／HVAC 深夜代接派工）改造成
PitLine——一支打電話進來的汽車經銷商售後服務部深夜代接 agent，加上一個即時
營運儀表板，兩者共同扮演 VaultDrive 在自己文案裡宣稱的角色：「凌駕於 DMS
之上、真正在跑店務的營運層」。

- 目標職缺：VaultDrive, Senior Full-Stack Developer (Founding Engineer, CTO Track)
- 可用時間：約 1 週
- 預估建置：6 個工作天 + 緩衝
- 預估成本：< US$40

## 目標與定位

VaultDrive 的 JD 和上一份鎖定「voice pipeline 工程能力」的職缺不同，它要的
是「AI agent 代表真實企業採取真實行動」的全端能力：JD 明確把 typed web
stack（前後端）、relational database、background job/queue system 列為
必要條件，voice/telephony 只是加分項而非必要條件。所以這次不是單純換個
system prompt 的表面重貼標籤——舊 demo 在 stack 覆蓋上有三個明確缺口
（沒有前端、沒有 queue、SQLite 而非正式的關聯式資料庫），這次要把這三個
缺口用同一組串起來的功能補上，而不是各自加一個象徵性的假功能。

沿用舊 demo 已經驗證過的核心主張——「我能自己維護一條即時語音管線、用真實
API 資料而非直覺做模型/延遲決策」——這部分保留 90% 不變。新加的東西全部
服務同一個敘事：DMS 是笨的舊系統，PitLine（agent + dashboard）才是真正
在做事、也讓人看得到它做了什麼的那一層。

## 場景設定

**售後服務部深夜代接**——直接對應 JD 原文「every service appointment...
passes through a DMS」：

1. 來電者描述車輛問題（煞車異音／方向盤異常／引擎過熱等）
2. agent 依固定規則判斷是否為安全性問題（Urgent case）
3. 呼叫工具查詢 DMS 的技師排班／可預約時段
   （Quick Service 技師 vs Master Technician，後者可支援前者但反之不行）
4. 訂下時段並複誦確認，或標記為安全性問題並模擬轉接真人服務顧問
5. 掛斷後，一個 Postgres queue table 裡的 Follow-up job 被排入，worker
   loop 撈出後透過 Twilio 發送確認／轉接簡訊
6. 整通電話期間，Ops Dashboard 透過 SSE 即時顯示新預約／安全性轉接／
   已排入的簡訊任務

完整詞彙定義見 [CONTEXT.md](../CONTEXT.md)。

## 技術架構

```
來電 (Twilio Media Streams)
  → Pipecat pipeline（沿用 VoiceAgent 骨架，不重寫）
      → Deepgram Nova-3（STT）
      → Claude + tool calling（新 system prompt / 新 tool schema，讀寫 DMS）
      → ElevenLabs Flash v2.5（串流 TTS）
  → 回撥 Twilio

Claude 的 tool calls
  → DMS API (FastAPI + Postgres)
      - Technician / Appointment（沿用 VoiceAgent 的 crm/，改 schema 措辭）
      - jobs table（新增，Follow-up job 佇列）
  → DMS API 寫入同時：
      - 記一筆 Appointment / Handoff
      - 排入一筆 Follow-up job（同一個 DB transaction）

Worker loop（新增，輪詢 jobs table）
  → 撈出待處理 Follow-up job → 呼叫 Twilio SMS API → 標記成功/重試

Ops Dashboard（新增，Next.js + TypeScript）
  → 透過 DMS API 的 SSE endpoint 訂閱即時事件
  → 唯讀：只顯示 DMS 狀態變化，沒有任何寫入路徑

全部部署在 Railway（沿用 VoiceAgent 的部署方式），Postgres 用 Railway 內建
的 add-on。
```

架構決策記錄：
[ADR-0001 DMS/ops-layer 定位](../docs/adr/0001-dms-ops-layer-mirrors-vaultdrive-architecture.md)、
[ADR-0002 Postgres queue table 而非 Redis](../docs/adr/0002-postgres-queue-table-not-redis.md)。

### 建置策略：改邏輯，不重寫管線

Pipecat pipeline 本身（bot.py 的骨架、silence_timeout.py、call_transfer.py、
thinking_filler.py、latency_observer.py）**直接沿用**，這些是與情境無關的
基礎設施，重寫沒有意義。真正要改的：

- `SYSTEM_INSTRUCTION`：安全性分類規則、Quick Service / Master Technician
  路由規則（Master Technician 可支援 Quick Service，反之不行）
- Tool schemas：措辭與欄位對齊新 DMS schema（含車輛 make/model/year 欄位）
- `crm/` → DMS：schema 從 SQLite 遷到 Postgres，新增 jobs table
- 新增：worker loop（Twilio SMS）、Next.js dashboard、SSE endpoint

**從舊專案學到、這次要修正的一件事**：舊 README 的「What's not done」明白
寫著——「a real phone call」從頭到尾沒有真的打通過，每個環節都只在各自的
單元／eval 層級驗證過。這次真人測試電話**提前到 Day 1 結束前**做一次最小
可行的 smoke test（哪怕邏輯還很粗），而不是留到 Day 5 才第一次真的打電話，
避免重蹈覆轍。

## 帳號與工具清單

- [x] Twilio 帳號 + 號碼（沿用 VoiceAgent 既有的）
- [x] Deepgram / Anthropic / ElevenLabs API key（沿用既有的）
- [ ] Railway Postgres add-on（新增）
- [ ] Twilio SMS 發送能力確認——**風險**：美加號碼的 A2P 10DLC 簡訊註冊
      有審核時間，trial 帳號或未註冊的號碼可能被過濾或直接失敗。Day 0
      就要確認帳號現況，不要留到 Day 3 才發現簡訊發不出去；備案是先用
      Twilio trial 允許發送的已驗證測試號碼收簡訊，或整段 demo 用
      email 取代 SMS（Follow-up job 的佇列/worker 邏輯不變，只換 side
      effect）。
- [ ] ngrok（開發期）或直接用 Railway 的 preview 部署

## 時程規劃

| 階段 | 重點 | 交付物 | 對應 stack 缺口 |
|---|---|---|---|
| Day 0（0.5 天） | 新 repo、複製/改造 VoiceAgent 可沿用的程式碼、Postgres schema（Technician/Appointment/jobs）、Railway 環境 | ngrok 打通 hello-world round trip；DMS API 跑在 Postgres 上 | relational DB |
| Day 1 | 新 system prompt、tool schema、安全性分類 + 技師路由邏輯；重跑 eval_dispatch_classification 方法論驗證新分類規則；**當天結束前打一次真人測試電話** | 邏輯可用、有真實 API 資料佐證的分類正確性 | — |
| Day 2（1–1.5 天） | 延遲調校、VAD 閾值、barge-in 驗證（沿用既有邏輯，換場景驗證仍成立） | timing log、模型比較數據 | — |
| Day 3（0.5–1 天） | edge case（聽不懂、沉默逾時、無可用時段）；**Follow-up job**：jobs table + worker loop + Twilio SMS，掛斷後觸發 | 一通完整電話會排入一筆簡訊任務並實際送出 | queue/background job |
| Day 4（1 天） | **Ops Dashboard**：Next.js + TS scaffold、DMS 端 SSE endpoint、唯讀即時畫面 | 通話進行中，儀表板即時跳出新預約／轉接 | typed web stack (前端) |
| Day 5（0.5 天） | 全鏈路真人測試電話（agent + DMS + queue + dashboard 一起跑在 Railway 上）、demo 錄影、延遲數據視覺化（真實資料） | 2–3 分鐘 demo 錄影 | — |
| 緩衝 | 除錯、README、申請信文案 | — | — |

**若時間不夠，砍的順序（已與你確認過）**：先砍 Dashboard（退而求其次用
截圖或錄影片段代替即時展示），再砍 Queue（README 誠實寫「沒做，原因是
X」），DMS 場景重貼標籤不砍，語音管線本身不砍——它是整個 demo 唯一不可
替代的核心主張。

## 成本估算

| 項目 | 費率 | demo 規模估算 |
|---|---|---|
| Twilio 號碼 + 通話 | 沿用既有 | < $3 |
| Twilio SMS（Follow-up job） | ~$0.0079/則 | < $1 |
| Deepgram STT | $0.01–0.02/分 | < $2 |
| Claude API | 依模型 | < $5 |
| ElevenLabs TTS | $0.015–0.03/分 | < $3 |
| Railway（Postgres + 3 個服務：agent/DMS/dashboard） | Hobby plan | ~$5–20 |
| **合計** | | **約 $20–35** |

## 風險與對策

**Turn-taking / 打斷**（沿用舊風險）：最容易吃掉時程。設 time-box，「堪用」
就停手。

**音訊格式**（沿用舊風險）：Day 0 就跑通最小 round trip。

**Twilio SMS 審核延遲**（新風險，見上）：Day 0 確認帳號現況，備案用 email。

**Postgres 遷移的意外**：舊 demo 是 SQLite，遷移到 Postgres 理論上只是
connection string 和少數方言差異，但 Day 0 就要跑一次完整遷移+seed，不要
假設「應該沒差」。

**SSE 連線的邊界情況**：dashboard 分頁保持開啟很久、或 API 重啟後，SSE
連線要能自動重連而不是靜靜卡死。Day 4 排一個小時測這個，不要只測「happy
path 有跳新資料就收工」。

**範圍蔓延**（沿用舊風險，這次更重要）：這次的 scope 本來就比上次大（多了
DMS 遷移、queue、dashboard 三塊新東西），更不能再往外加功能。不做跨通話
記憶、不做 dashboard 的任何寫入功能、不做真人轉接的完整實作。

## 交付物 / demo 成功標準

- [ ] 一通能真的打進去、agent 全程對話、能查詢並寫入 DMS 的通話
- [ ] agent 能被打斷且不失控
- [ ] 一次安全性問題（Urgent case）觸發轉接模擬，一次一般預約成功完成
- [ ] 掛斷後有一則真的透過 Twilio 送出的確認/轉接簡訊
- [ ] Ops Dashboard 在通話進行中即時顯示新預約/轉接
- [ ] 延遲數據視覺化（真實通話資料，不是合成資料）
- [ ] 2–3 分鐘 demo 錄影（涵蓋以上全部）
- [ ] README 說明架構，並明確點出這個 demo 如何對應 JD 的每一條必要條件

## 後續：申請信文案

demo 完成、有真實素材（真的通話錄音、真的延遲數據、真的簡訊截圖）之後再
寫申請信文案，重點會是：為什麼選這個場景（JD 自己點名的 service
appointment + DMS）、為什麼用這個架構（correctness not cleverness——固定
分類規則、eval 驗證過的模型選擇、queue 保證簡訊不會因為單次失敗就消失）、
以及 CTO-track 這件事在文案裡怎麼呼應（架構決策記錄本身，即 docs/adr/，
就是「我怎麼做技術決策」的直接證據，可以附連結而不用另外重述一遍）。

---
*規劃於 grill-with-docs 對話中，實作在獨立新專案 PitLine 進行。*

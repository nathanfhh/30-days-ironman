# Day 1 首頁 roadmap 圖 — 生成 prompt

- **落點**：`articles/day-01.md`，「這三十天要寫什麼」段落底下（BookStack page id 3）
- **生成工具**：ChatGPT（GPT Image / imagen 系列），手動貼上生成
- **為什麼不是自動生成**：Codex CLI 沒有 image generation 能力，本機也沒有 `OPENAI_API_KEY`。這張圖的生成步驟是人工的，所以 prompt 必須留檔——否則重畫一次就得重想一次。

---

## 設計意圖

圖要對齊的是 **Day 1 自己寫的那四段**，不是 BookStack 的三個章節。理由：讀者是在「這三十天要寫什麼」這段文字底下看到這張圖，圖跟它正上方的文字不一致，就是在製造困惑。

| 段 | 對應天數 | 這一段的命題 |
|---|---|---|
| 1. Skill 怎麼從零搭起來 | Day 3–11 | 把一個人的審查判斷寫成檔案 |
| 2. 怎麼知道它有沒有變壞 | Day 12–16 | 散文改一句，行為就會悄悄消失——所以要測、要評分、要知道規則何時該拿掉 |
| 3. 環境的邊界 | Day 17–20, 23 | 可拋棄的容器、憑證借進來還回去、網路開多大 |
| 4. 看得見與收得回 | Day 21–22, 24–30 | 成本歸因、流量側錄，最後搬上網頁終端機 |

貫穿的敘事弧：**一個很好心的 AI 把資料庫密碼寫進設定檔 → 於是我花三十天，替它蓋一個做同樣的事也不會出事的地方。**

---

## ⚠ 先讀這一段：中文字不要交給模型畫

影像生成模型畫中文字幾乎必然出錯——筆畫崩壞、造字、字序錯亂。這張圖如果讓模型直接畫「Skill 怎麼從零搭起來」，出來的字有很高機率是不存在的漢字，而這是文章首圖，錯字的成本比圖醜高得多。

**採用策略：模型只畫視覺骨架，一個字都不畫；文字後製疊上。**

主 prompt 已寫死 `no text, no letters, no numbers, no labels anywhere`。生成後的疊字有兩條路：

- Figma / Keynote / 任何向量工具，手動疊四段標題（最快）
- 或告訴我，我用 SVG 把圖包起來、文字用 `<text>` 疊在固定座標上，可版控可 diff

如果你想試「讓模型連字一起畫」，用下面的 **變體 B**，但**務必逐字檢查每一個漢字**。

---

## 主 prompt（變體 A：無文字，建議用這個）

```
A wide horizontal editorial illustration for a software engineering blog series,
16:9 aspect ratio, flat vector style with subtle grain texture.

A single continuous path travels left to right across the frame, divided into four
distinct segments by three vertical transitions. The path starts thin and uncertain
and becomes thicker and more structured as it moves right.

Segment 1 (leftmost): scattered loose geometric fragments — small squares and lines
drifting apart — gradually being gathered and stacked into a neat solid block by the
end of the segment. Represents assembling something from nothing.

Segment 2: the solid block now sits on a measuring apparatus — a balance scale or
caliper form — with fine gridlines and small check marks around it. One fragment is
being deliberately lifted away from the block and set aside. Represents verification
and deliberate removal.

Segment 3: the block is enclosed inside a translucent rounded container with a clearly
drawn boundary wall. A few arrows approach the wall from outside; some pass through a
single narrow gate, others stop at the wall. Represents a controlled perimeter.

Segment 4 (rightmost): the container is connected upward to a floating rectangular
screen or window frame, with thin connecting lines and small circular gauge shapes
alongside. Represents making it observable and reachable over the network.

Color: restrained palette — deep slate blue and warm off-white as the base, with a
single warm amber accent used sparingly to mark the four transition points. Muted,
professional, not childish, not corporate-clipart.

Absolutely no text, no letters, no numbers, no labels anywhere in the image.
Clean negative space at the top and bottom for captions to be added later.
```

## 變體 B：含英文標籤（風險較低但仍要檢查）

在變體 A 的 prompt 末尾，把最後兩行換成：

```
Include only these four short English labels, one under each segment, in a clean
geometric sans-serif: "BUILD", "VERIFY", "CONTAIN", "OBSERVE".
No other text anywhere. Spell them exactly as given.
```

英文短詞的出錯率遠低於中文，但仍會出現字母重複或缺漏。**生成後逐字比對這四個詞。**

---

## 驗收清單

貼回來之前先自己看過：

- [ ] 圖裡沒有任何非預期的文字／符號（變體 A 應該是零文字）
- [ ] 四個段落在視覺上分得出來，而且順序是左到右
- [ ] 第 3 段的「邊界」看得出是有選擇性的通過，不是全擋或全開
- [ ] 16:9 左右，寬圖，放進文章不會太高
- [ ] 沒有生出可辨識的品牌 logo（模型有時會把 Docker 鯨魚之類的東西畫進去）

---

## 生成紀錄

生成後請把實際使用的模型與參數補在這裡，下次要重畫才有依據。

| 日期 | 模型 | 變體 | 結果 | 備註 |
|---|---|---|---|---|
| | | | | |

# Assignment 4：法規知識圖譜與問答系統

## 一、專案簡介

本專案的目標是建立一套以法規文件為基礎的本地端問答系統。系統先將法規 PDF 轉換為結構化資料，再將其建構成 Neo4j 知識圖譜（Knowledge Graph, KG），最後透過圖譜中的規則節點進行檢索，並生成對應答案。

整體流程如下：

**PDF 法規文件 → SQLite 結構化資料 → Neo4j 知識圖譜 → 規則檢索 → 答案生成 → 自動評測**

在本次作業中，我完成了知識圖譜中 `Rule` 節點的建構、調整了本地模型載入方式，並修改查詢系統，使其能先從圖譜中檢索規則，再根據規則內容回答問題。

---

## 二、系統流程說明

本專案主要包含以下幾個步驟：

1. **`setup_data.py`**  
   讀取原始法規 PDF，擷取文字並整理為結構化資料，儲存到 SQLite 資料庫中。

2. **`build_kg.py`**  
   從 SQLite 讀取法規與條文資料，建構 Neo4j 知識圖譜。

3. **`query_system.py`**  
   根據使用者問題，從 Neo4j 中檢索相關規則，並生成 grounded answer。

4. **`auto_test.py`**  
   使用測試題目與自動評分流程，評估問答系統表現。

---

## 三、KG Schema 設計

### 3.1 Schema 整體設計

本知識圖譜採用三層式結構：

- **`Regulation`**：代表一份法規文件
- **`Article`**：代表法規中的條文
- **`Rule`**：代表從條文內容中抽取出的細粒度規則

圖譜中的主要關係如下：

- **`(Regulation)-[:HAS_ARTICLE]->(Article)`**
- **`(Article)-[:CONTAINS_RULE]->(Rule)`**

也就是說，本圖譜的核心結構可表示為：

**Regulation → Article → Rule**

此設計可同時保留法規的原始層級架構，以及支援規則層級的精細檢索。

### 3.2 為什麼採用這種 Schema

我採用這種設計的原因，是因為法規問答不只需要保留文件原始結構，也需要能夠精準找到具體規則內容。

- `Regulation` 節點用來保留法規文件層級資訊
- `Article` 節點用來保留每一條條文的完整內容
- `Rule` 節點則用來將條文切分成較小、可檢索的規則單位

這樣的設計比只用條文全文搜尋更有效，因為許多問題實際上是詢問：

- 某項規定是否允許
- 遲到幾分鐘不能考試
- 忘記帶證件的處罰是什麼
- 成績及格標準是多少
- 修業年限最長可以延長多久

這些問題通常需要的是「規則層級」資訊，而不是整篇條文。

---

## 四、節點與關係設計

### 4.1 `Regulation` 節點

`Regulation` 節點代表一份法規文件。

**主要屬性：**
- `id`
- `name`
- `category`

### 4.2 `Article` 節點

`Article` 節點代表法規中的單一條文。

**主要屬性：**
- `number`
- `content`
- `reg_name`
- `category`

### 4.3 `Rule` 節點

`Rule` 節點代表從條文中進一步抽取出的細粒度規則。

**主要屬性：**
- `rule_id`
- `type`
- `action`
- `result`
- `art_ref`
- `reg_name`

其中：

- `type` 表示規則類型，例如 obligation、permission、penalty 等
- `action` 表示規則條件或行為描述
- `result` 表示結果、限制或處分內容
- `art_ref` 表示對應條文編號
- `reg_name` 表示規則所屬法規名稱

### 4.4 關係設計

本知識圖譜包含兩種主要關係：

- **`HAS_ARTICLE`**：將法規連接到其所包含的條文
- **`CONTAINS_RULE`**：將條文連接到從條文抽取出的規則

透過這兩種關係，系統可以從法規文件逐層走訪至條文，再進一步找到對應的具體規則。

---

## 五、KG 建構過程

在實作過程中，我發現原始提供的 `build_kg.py` 模板並沒有真正完成 `Rule` 節點的建立。  
因此，我補上了以下步驟：

- 逐一讀取 SQLite 中的 article 資料
- 根據 article 內容抽取 rule candidates
- 為每一條規則建立唯一的 `rule_id`
- 建立 `Rule` 節點
- 以 `CONTAINS_RULE` 將 `Article` 與 `Rule` 連接起來

為了先讓整體流程可以順利執行，我先採用了 deterministic fallback rule extraction 的方式，將 article 內容切分為句段，再轉為結構化規則資料。

完成後，Neo4j preflight check 顯示已成功建立 `Rule` 節點，代表圖譜已具備 Rule-level retrieval 的能力。

---

## 六、查詢與檢索設計

在 `query_system.py` 中，我也補上了原本模板中尚未完成的查詢流程。

目前查詢流程如下：

1. 正規化使用者問題
2. 擷取問題中的重要關鍵詞與類型
3. 優先檢索 `Rule` 節點
4. 若 rule-level 檢索不足，再 fallback 至 article-level 檢索
5. 根據題目特徵對結果進行重新排序
6. 根據檢索到的規則內容生成簡短且 grounded 的答案

這種 **rule-first retrieval** 的方式，比只搜尋整段 article text 更適合回答法規題目，尤其對於數值、時間限制、處分、允許與否等問題效果較佳。

---

## 七、Graph 截圖說明

以下是本報告應附上的知識圖譜截圖，建議使用 Neo4j Browser 執行對應查詢後截圖。

> **說明：**  
> 請將你實際的 Neo4j Browser 截圖放入對應位置，或替換成你自己的圖片檔案路徑。

### 圖 1：KG 整體結構圖

此圖應顯示三層結構：

**Regulation → Article → Rule**

![KG整體結構圖](screenshots/kg_overall_structure.png)

**建議使用的 Cypher 查詢：**

```cypher
MATCH (r:Regulation)-[:HAS_ARTICLE]->(a:Article)-[:CONTAINS_RULE]->(ru:Rule)
RETURN r, a, ru
LIMIT 12;
```

## 圖 2：Regulation 與 Article 的關係

此圖可顯示法規文件如何連結到各條文。

**建議使用的 Cypher 查詢：**

```cypher
MATCH (r:Regulation)-[:HAS_ARTICLE]->(a:Article)
RETURN r, a
LIMIT 15;
```

## 圖 3：Article 與 Rule 的關係

此圖可顯示條文如何對應到較細粒度的規則節點。

**建議使用的 Cypher 查詢：**

```cypher
MATCH (a:Article)-[:CONTAINS_RULE]->(ru:Rule)
RETURN a, ru
LIMIT 15;
```

## 圖 4：Rule 節點屬性示意圖

此圖可展示 `Rule` 節點內部屬性，例如：

- `rule_id`
- `type`
- `action`
- `result`
- `art_ref`
- `reg_name`

**建議使用的 Cypher 查詢：**

```cypher
MATCH (ru:Rule)
RETURN ru
LIMIT 5;
```

---

## 八、截圖說明文字範例

上述截圖顯示，本研究成功建立了以 `Regulation`、`Article`、`Rule` 為核心的階層式知識圖譜。  
其中，`Regulation` 透過 `HAS_ARTICLE` 與 `Article` 連結，`Article` 再透過 `CONTAINS_RULE` 與 `Rule` 節點連結。這樣的設計同時保留了法規文件的原始層級結構與可供檢索的細粒度規則結構。

此外，`Rule` 節點中所儲存的 `type`、`action`、`result`、`art_ref` 與 `reg_name` 等資訊，使系統能夠更精準地回答與法規規則有關的問題，而不只是依賴整段條文文字做全文搜尋。

---

## 九、實作過程中的問題與改進方向

本專案在實作過程中遇到的主要問題包括：

1. **原始模板未完成 Rule 建構**  
   最初圖譜中只有 `Regulation` 與 `Article`，沒有真正建立 `Rule` 節點，因此 auto-test 無法通過。

2. **查詢模板未完成**  
   `query_system.py` 中的檢索與回答流程原本仍是 TODO，因此需要自行補完。

3. **本地模型執行速度與平台穩定性問題**  
   為了使系統能在本機環境執行，我調整了模型載入與回答流程，優先確保整體 pipeline 可以跑通，再逐步優化速度與正確率。

未來仍可進一步改進的方向包括：

- 將 fallback rule extraction 升級為更完整的 LLM-based extraction
- 強化 rule deduplication
- 提升問題類型判斷能力
- 針對數值題、處分類題目設計更精準的 reranking 策略

---

## 十、結論

本專案成功建立了一個以 `Regulation`、`Article`、`Rule` 為三層核心結構的法規知識圖譜。  
此設計不僅保留了法規文件的層級結構，也提供了規則層級的細粒度檢索能力，有助於後續進行更精準的法規問答。

與只依賴 article-level 全文搜尋相比，這種 schema 更適合處理法規問答中常見的具體問題，例如：

- 處分與罰則
- 時間與期限限制
- 修業年限
- 成績及格標準
- 是否允許某種行為

因此，本知識圖譜可作為 regulation-aware retrieval 與 grounded answer generation 的有效基礎。

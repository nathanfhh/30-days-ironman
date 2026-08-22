"""DB engine / session factory（ADR 0008）。

這套東西的資料庫**就是 SQLite**，不支援第二種方言。單機部署、檔案級鎖、
備份＝複製一個檔案——這正是它要的形狀：DB 是跨 worker 的唯一仲裁者，而
仲裁靠的是「一顆檔案、一把寫鎖」這個最簡單的模型。

互斥只有一條路：**BEGIN IMMEDIATE**（見 `_sqlite_begin`）。所有「檢查再動作」
（搶 port、算配額、租約接手）都必須走 `session_scope(immediate=True)`，
在交易一開始就取得寫鎖，讓整段「讀-判斷-寫」真正互斥。沒有第二套機制，
所以這條路必須被測試釘住，不能靠「反正只有一個 worker」。

⚠ **但「需不需要互斥」不是唯一的判準，甚至不是最常咬人的那個。** WAL 之下 deferred 交易
  先讀後寫時，中途只要有別人 commit 過，升級寫鎖會**當場**回 `SQLITE_BUSY`，而
  `busy_timeout` 對這種快照衝突無效（它等的是鎖，不是衝突）——`auth.create_user` 上面
  記著量過的數字：4 併發 × 20 輪、12.5% 回 500 `database is locked`。這個機制與互斥無關，
  **只要先 SELECT 後 UPDATE 就中**。全樹有 21 處是這個形狀（審查 F-024），其中
  `touch` / `resize` / `rename` / 改密碼 / 設 token 都在使用者路徑上。

  逐條追過之後，那 21 處**沒有一處會產生「兩個行程同時通過檢查」的靜默錯誤結果**
  （全是後寫者贏且兩值皆對、冪等、或由租約保證單寫者），所以這不是資料正確性問題，
  是偶發 500。真正要改的是判準：**「這筆交易會不會寫」比「需不需要互斥」更接近機制。**
  SQLite 本來就單寫者，寫交易一律 IMMEDIATE 幾乎沒有額外成本。

  **2026-08-11：使用者路徑上的 12 處已經照這個判準改掉**（`auth` 六處、`sessions` 的
  create/rename/probe_container/touch、`views` 的 open_view/close_views）。動手的原因不是
  巡邏，是真的撞到：`POST /api/sessions` 回 500 `database is locked`，而 `create()` 的
  `except` 補償把**已經 start 起來的容器拆掉**——「偶發 500」在那一處的實際後果是開場失敗。
  同一支測試量到的比原本記的更嚴重：4 併發 × 20 輪、**80 次裡 40 次**（改完 0 次）。
  `test_mutex_semantics` 有兩條守著：一條真的開執行緒去撞，一條靜態擋「別再退回 deferred」。

  ⚠ 唯一要另外想的是 `reconciler` 的 step 0（讀全表再逐列寫）：它會在整個迴圈期間持寫鎖
    擋住 web 的寫入，那一支該改成「先讀一份清單、再逐列小交易寫」而不是直接加 immediate。
    在那之前它維持現狀——被 SQLITE_BUSY 丟掉重來的代價是整輪對帳，值得先想清楚再動。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager, suppress

from sqlalchemy import UniqueConstraint, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

_engine = None
_SessionFactory = None
_tls = threading.local()  # 標記本執行緒接下來的交易是否需要 BEGIN IMMEDIATE


def _make_engine(url: str):
    # 檔案型 SQLite（非 :memory:）：確保放檔的目錄存在，否則 connect 直接失敗
    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    engine = create_engine(
        url,
        future=True,
        echo=False,
        # check_same_thread=False：Flask threaded=True 下同一 connection 可能跨 thread 取用；
        # 交易邊界由 session_scope 控管。timeout＝忙碌時等鎖的秒數（配合 WAL 降低 SQLITE_BUSY）。
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 由連線觸發
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # 多 reader + 單 writer 併行（多 worker 前提）
        cur.execute("PRAGMA busy_timeout=15000")  # 撞鎖時等待而非立刻拋 SQLITE_BUSY
        cur.execute("PRAGMA foreign_keys=ON")  # SQLite 預設不強制 FK，須顯式開啟
        cur.close()
        # 關掉 pysqlite 的隱式 BEGIN，交易改由下面的 begin 事件明確發出——
        # 否則無法指定 BEGIN IMMEDIATE（review B2）。
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):  # pragma: no cover - 由交易觸發
        # SQLite 的預設交易是 deferred：先讀時只拿 read lock，之後要寫才升級成 write
        # lock——所以「SELECT COUNT 再 INSERT」不可序列化，兩個執行緒可同時通過配額
        # 檢查（review B2 指出：不只多 worker，單一 threaded process 內就會發生）。
        # IMMEDIATE 在交易一開始就取寫鎖，讓整段「檢查＋寫入」真正互斥。
        conn.exec_driver_sql("BEGIN IMMEDIATE" if getattr(_tls, "immediate", False) else "BEGIN")

    return engine


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine(config.DB_URL)
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), class_=OrmSession, expire_on_commit=False, future=True)
    return _SessionFactory


@contextmanager
def session_scope(immediate: bool = False):
    """一筆交易的邊界：正常結束 commit，例外 rollback，最後必關。

    DB 是跨 worker 的唯一仲裁者（ADR 0008），所有「檢查再動作」（搶 port、算上限）
    都必須在同一個 session_scope 內完成，才有原子性。

    immediate=True：該交易需要「讀-改-寫」的互斥（如配額檢查、租約接手）。
    交易起始即取整個 DB 的寫鎖（BEGIN IMMEDIATE，見 _sqlite_begin 的說明）。
    這是本系統唯一的互斥機制——漏標 immediate 的症狀不是報錯，是兩個行程
    同時通過檢查。
    """
    prev = getattr(_tls, "immediate", False)
    _tls.immediate = immediate
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        _tls.immediate = prev


def init_db() -> None:
    """建表 + 補欄位（皆冪等）。

    `create_all` 只建「不存在的表」，不會改既有表——DB 放在 volume 持久化後，新增欄位
    會讓舊資料庫直接爆 `no such column`（2026-07-25 實測踩到）。這裡做**僅限新增欄位**
    的輕量升級；一旦出現改名/改型別/刪欄位這類非新增變更，就該引入 alembic，別硬撐。

    ⚠ 能力邊界比字面更窄：**只補欄位本身**。既有表上新增的 index、UNIQUE/CHECK
    constraint 與 **ForeignKey** 一律不會被套用，而且是**靜默的**（不報錯，只是永遠
    沒生效）。外鍵尤其容易被漏掉：`CreateColumn` 只 render 欄位規格（型別 / DEFAULT /
    NOT NULL），FK 是 table-level 約束，只在 CREATE TABLE 時才會出現——所以在**已經
    跑起來的**部署上，帶 ForeignKey 的新欄位會變成一個普通的 INTEGER，`ondelete`
    行為永遠不生效（例：SessionHistory.ended_by_user_id 的 SET NULL）。
    因此「新表帶約束」沒問題（走 create_all），「既有表加約束」就必須引入 alembic。

    ⚠ 「冪等」不等於「併發安全」，而這個部署**同時有兩個行程在跑它**：control 在 import
      時跑（app.py），reconciler 在啟動時跑（reconciler.py），而 compose 的
      `depends_on: [control]` 只等容器 start、不等 app ready。`create_all` 的 checkfirst
      與下面的「先 inspect 再 ALTER」都是 check-then-act，中間沒有鎖——加欄位的那一次部署
      裡，慢的那一方會撞上 `duplicate column name` / `table already exists` 而整個行程掛掉。
      `restart: unless-stopped` 會把它救回來，所以看起來只是重啟一下，但那是**真的崩過**。
      兩種錯誤在語意上都代表「別人已經做好了」，所以吞掉是正確的，不是掩蓋。
    """
    engine = get_engine()
    with suppress(OperationalError):
        Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    with suppress(Exception):  # noqa: BLE001 — 這只是診斷，不可以擋住啟動
        _warn_missing_constraints(engine)


def _add_missing_columns(engine) -> None:
    from sqlalchemy import inspect
    from sqlalchemy import text as _text
    from sqlalchemy.schema import CreateColumn

    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            # 用 SQLAlchemy 自己的 compiler 產生欄位定義，不要手拼字串：server_default
            # 該不該加引號由方言決定，交給 compiler 才不會產出型別對不上的 DEFAULT。
            ddl = str(CreateColumn(col).compile(engine))
            if not col.nullable and col.server_default is None:
                # 既有列填不出值，只能先放寬；真要 NOT NULL 得走 alembic 補資料再改
                ddl = ddl.replace(" NOT NULL", "")
            try:
                with engine.begin() as conn:
                    conn.execute(_text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            except OperationalError as e:
                # 另一個行程（control ⇄ reconciler 同時啟動）搶先補上了同一個欄位。
                # 「已經存在」正是我們要的結果，不是錯誤——但其他 DDL 失敗要照樣炸出來，
                # 所以只吞這一種訊息，不吞整類例外。
                if "duplicate column" not in str(e).lower():
                    raise
                continue
            print(f"[claude-pty] schema：{table.name} 補上欄位 {col.name}", flush=True)
            if col.index or col.unique or col.foreign_keys:
                # ALTER ADD COLUMN 帶不出 index / UNIQUE / FOREIGN KEY，而且不會有任何
                # 錯誤——講出來，否則下一個人會以為約束生效了（review S8）。
                # ⚠ foreign_keys 尤其容易漏：CreateColumn 只 render 欄位規格，實測
                #   `ended_by_user_id INTEGER`，REFERENCES 與 ON DELETE 全掉了。
                print(
                    f"[claude-pty] ⚠ {table.name}.{col.name} 宣告了 "
                    f"index/unique/foreign key，但既有表的索引與約束無法由輕量升級"
                    f"補上，需要 alembic",
                    flush=True,
                )


def _warn_missing_constraints(engine) -> None:
    """既有表少了 `__table_args__` 宣告的 UNIQUE 就大聲講——**只報不補**。

    ⚠ `_add_missing_columns` 只對**它自己新增的欄位**檢查 index/unique/foreign_keys 並警告；
      寫在 `__table_args__` 裡的 table-level 約束（`uq_views_port`、`uq_views_session`、
      `uq_history_session`）完全不在那個範圍內，而 `create_all` 又跳過既有表（審查 F-039）。
      少了 views 那兩條的部署，`_claim_port` 的 IntegrityError 分岔永遠不觸發、`_PEER` 那條
      路是死的，兩個 worker 會為同一個 session 各起一顆 ttyd（review H1 要防的正是這件事）
      ——而**沒有任何訊息**。

    ⚠ **不自動補**：ALTER TABLE 加不了 UNIQUE，真的要補得走 alembic 重建表。這裡的職責與
      欄位那一半一致——把「輕量升級做不到的事」講出來，讓人決定，而不是假裝做到了。
    """
    from sqlalchemy import inspect as _inspect
    from sqlalchemy import text as _text

    insp = _inspect(engine)
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            want = {c.name for c in table.constraints if isinstance(c, UniqueConstraint) and c.name}
            if not want or not insp.has_table(table.name):
                continue
            with suppress(OperationalError):
                have = {r[1] for r in conn.execute(_text(f"PRAGMA index_list('{table.name}')")).fetchall() if r[2]}
            # SQLite 對 `UNIQUE` 生的是 sqlite_autoindex_*，名字對不上宣告的 name，
            # 所以比**數量**而不是比名字——少了幾條就是少了幾條。
            if len(have) < len(want):
                print(
                    f"[claude-pty] ⚠ {table.name} 少了 table-level 的 UNIQUE 約束"
                    f"（宣告 {len(want)} 條、實際 {len(have)} 條）。輕量升級補不上它"
                    f"（ALTER TABLE 加不了 UNIQUE），需要 alembic 重建表。"
                    f"views 少了它的症狀是兩個 worker 為同一 session 各起一顆 ttyd，"
                    f"而且沒有任何錯誤訊息。",
                    flush=True,
                )


def reset_engine() -> None:
    """丟棄現有 engine/factory（測試切換 DB_URL 後呼叫）。"""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None

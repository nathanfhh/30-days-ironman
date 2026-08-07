"""DB engine / session factory（ADR 0008）。

這套東西的資料庫**就是 SQLite**，不支援第二種方言。單機部署、檔案級鎖、
備份＝複製一個檔案——這正是它要的形狀：DB 是跨 worker 的唯一仲裁者，而
仲裁靠的是「一顆檔案、一把寫鎖」這個最簡單的模型。

互斥只有一條路：**BEGIN IMMEDIATE**（見 `_sqlite_begin`）。所有「檢查再動作」
（搶 port、算配額、租約接手）都必須走 `session_scope(immediate=True)`，
在交易一開始就取得寫鎖，讓整段「讀-判斷-寫」真正互斥。沒有第二套機制，
所以這條路必須被測試釘住，不能靠「反正只有一個 worker」。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager, suppress

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

_engine = None
_SessionFactory = None
_tls = threading.local()   # 標記本執行緒接下來的交易是否需要 BEGIN IMMEDIATE


def _make_engine(url: str):
    # 檔案型 SQLite（非 :memory:）：確保放檔的目錄存在，否則 connect 直接失敗
    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    engine = create_engine(
        url, future=True, echo=False,
        # check_same_thread=False：Flask threaded=True 下同一 connection 可能跨 thread 取用；
        # 交易邊界由 session_scope 控管。timeout＝忙碌時等鎖的秒數（配合 WAL 降低 SQLITE_BUSY）。
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 由連線觸發
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # 多 reader + 單 writer 併行（多 worker 前提）
        cur.execute("PRAGMA busy_timeout=15000")  # 撞鎖時等待而非立刻拋 SQLITE_BUSY
        cur.execute("PRAGMA foreign_keys=ON")     # SQLite 預設不強制 FK，須顯式開啟
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
        conn.exec_driver_sql(
            "BEGIN IMMEDIATE" if getattr(_tls, "immediate", False) else "BEGIN")

    return engine


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine(config.DB_URL)
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), class_=OrmSession, expire_on_commit=False, future=True
        )
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
                print(f"[claude-pty] ⚠ {table.name}.{col.name} 宣告了 "
                      f"index/unique/foreign key，但既有表的索引與約束無法由輕量升級"
                      f"補上，需要 alembic", flush=True)


def reset_engine() -> None:
    """丟棄現有 engine/factory（測試切換 DB_URL 後呼叫）。"""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None

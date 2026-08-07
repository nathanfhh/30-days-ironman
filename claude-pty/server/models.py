"""持久化 registry 的 SQLAlchemy models（ADR 0008）。

三張表，壽命與職責分明：
  users    — 帳號（argon2id 雜湊），authn/authz 地基（ADR 0005）
  sessions — session registry，container 為王（ADR 0007：DB 是便利/路由層，真相在
             container + ~/.claude mount）。不存 ttyd pid——ttyd 屬於「一次觀看」。
  views    — on-demand ttyd 的暫態記錄；port 加 UNIQUE 由 DB 當跨 worker 的分配仲裁。
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> _dt.datetime:
    """timezone-aware UTC；DB 一律存 UTC，顯示時才轉本地時區。"""
    return _dt.datetime.now(_dt.UTC)


class UtcDateTime(TypeDecorator):
    """永遠以 UTC-aware datetime 進出的 DateTime。

    SQLite 沒有原生 timezone 型別，`DateTime(timezone=True)` 寫進去的 tzinfo 讀回來會掉，
    之後拿 naive 值跟 aware 的 utcnow() 相比會直接 TypeError（idle 回收就會踩到）。
    這裡統一：寫入前轉成 UTC，讀出後補回 UTC tzinfo。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:            # 容忍呼叫端給 naive，一律當 UTC
            return value.replace(tzinfo=_dt.UTC)
        return value.astimezone(_dt.UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:            # SQLite 讀回為 naive → 補回 UTC
            return value.replace(tzinfo=_dt.UTC)
        return value.astimezone(_dt.UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # argon2id 雜湊字串（含參數與 salt）。絕不存明文、不用 sha256/md5（ADR 0008）。
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 每次改密碼 +1；登入 cookie 帶當下版號，改密碼後舊 cookie 立即失效（review H4）
    # server_default 不可省：既有資料庫用 ALTER TABLE ADD COLUMN 補這欄時，NOT NULL 需要
    # 一個 DB 端的預設值才填得進去（純 Python 端的 default 對 ALTER 無效）。
    password_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1")
    # 這個人開終端時要用哪一顆 ttyd（C 版 `ttyd` / Rust 重寫 `ttyd-rust`），由管理畫面的
    # 「設定」切換。**做成 per-user 而不是全域**：它的用途是 A/B 比較，全域的話一個人切換
    # 會把別人正在比的終端一起換掉。NULL＝沒設過 → 用 config.TTYD_BIN_DEFAULT。
    # nullable 所以既有列的 ALTER TABLE ADD COLUMN 不需要 server_default。
    # ⚠ 這個值會變成 argv[0]，讀出來一律經 config.ttyd_bin_or_default() 收斂，不可直接 exec。
    ttyd_bin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    # passive_deletes 讓 DB 的 ON DELETE CASCADE 生效；否則 SQLAlchemy 預設會試圖把子列的
    # user_id 設為 NULL，而該欄為 NOT NULL → 刪使用者直接失敗（review M3）。
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True)


# session.status 的合法值。DB 狀態會與 dockerd 漂移，由 reconciler 對帳（ADR 0008）。
STATUS_CREATING = "creating"      # 已佔登錄、container 尚未就緒
STATUS_RUNNING = "running"
STATUS_EXITED = "exited"          # container 自行結束（claude /exit、crash）
STATUS_TERMINATED = "terminated"  # 使用者明確終止

# session_history.ended_reason 的合法值（ADR 0010）。與 STATUS_* 放一起，因為它們是
# 同一件事的兩面：這裡記的是「當初為什麼離開 sessions 表」。
END_TERMINATED = "terminated"      # 使用者按了終止
END_EXITED = "exited"              # container 自行結束（CLI /exit、crash）
END_GONE = "gone"                  # container 從外部消失（docker rm、prune、host 重啟）
END_IDLE = "idle"                  # idle 回收（預設停用）


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_CREATING)
    # 使用者自取的名字，純為辨識（12 位 hex 的 sid 記不住）。正規化後的版本會接在
    # container 名稱尾巴，讓 `docker ps` 也一眼認得出來。
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 建立當下的 cwd（ADR 0007：對話續命靠 ~/.claude mount + 一致 cwd）。
    # ⚠ 值永遠是 config.WORKDIR，所以**不進 session 的 API 回應**——每一列都一樣的欄位
    #   對「當下狀態」是零資訊。留著是為了歷史快照：日後改了 CLAUDE_PTY_WORKDIR，
    #   舊紀錄才答得出「那場跑在哪」。欄位本身不可移除（NOT NULL 且無 server_default，
    #   拿掉之後既有 DB 的 INSERT 會直接失敗——本專案的 schema 升級只支援新增欄位）。
    workdir: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 執行 profile（ADR 0006）。JSON 欄位（SQLite 實際存 TEXT，SQLAlchemy 負責序列化），
    # 列表篩選會用 JSON 取值運算子去比對裡面的值。
    #
    # 對上層而言這裡就是 **dict**：序列化是 DB 邊界的事，呼叫端不該看到 json.dumps/loads。
    # 欄位名維持 `profile_json` 以免動到既有 DB；屬性名是 `profile`，因為那才是它的型別。
    # ⚠ JSON 欄位**偵測不到就地修改**（`row.profile["cli"] = x` 不會被寫回）。一律整份
    #   指派；真的需要就地改再引入 MutableDict。
    profile: Mapped[dict] = mapped_column(
        "profile_json", JSON(), nullable=False, default=dict)
    created_at: Mapped[_dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    # idle 回收與「最後活動」顯示用；開 view / 改尺寸時更新
    last_active_at: Mapped[_dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    # 目前的終端尺寸。不是裝飾性的：「觸發重繪」是靠改尺寸送 SIGWINCH，
    # 改完必須還原成原值——不記得原值就沒得還原
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=40, server_default="40")
    cols: Mapped[int] = mapped_column(Integer, nullable=False, default=140, server_default="140")
    # 第一次被觀察到「就緒」的時刻。與 created_at 相減＝啟動耗時，那是這個系統最值得盯的
    # 數字：restricted profile 的 trivy DB 沒命中快取時會從 1 秒暴增到 36 秒，沒有這個
    # 欄位就只能靠人盯著碼表才發現。NULL＝還沒就緒過（或建立於此欄位存在之前）。
    ready_at: Mapped[_dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # 最後一次**真的問到 dockerd** 的容器狀態，與問到的時刻（ADR 0013）。
    # 列表不再自己打 docker（一顆卡住的容器會拖垮整張表），改讀這兩欄並把新鮮度顯示
    # 出來——「這是兩分鐘前跟 dockerd 求證過的」是誠實的，「看起來即時、其實卡住了」不是。
    # ⚠ **唯一的寫入者是 reconciler。** 單筆查詢（status）問到即時狀態只放進 response，
    #   **絕不寫 DB**：`/api/auth/view` 是 nginx 的 auth_request 掛載點，每開一次終端
    #   併發打 4~5 發，一旦變成寫入交易就會撞出 500 `database is locked`（實際發生過，
    #   上線 30 分鐘就炸）。這裡原本寫「問到時也順手更新」，那正是那個回歸。
    # NULL＝從來沒問到過（剛建立、或建立於這兩欄存在之前）→ 前端顯示為尚未確認。
    # 兩欄都 nullable，所以既有列的 ALTER TABLE ADD COLUMN 不需要 server_default。
    docker_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state_checked_at: Mapped[_dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # 啟動當下容器裡的 CLI 版本（`claude --version` 的輸出）。
    # ⚠ 是**快照**不是現況：CLI 會在容器內自我更新（畫面上那行 "Auto-update failed"
    #   正是它在試），跑久了可能與這裡不同。它回答的是「這場是用哪一版開起來的」。
    #   刻意問二進位檔而不是讀畫面上的版本號——TUI 排版會變，解析畫面遲早會錯。
    cli_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 這場所用 image 的打包時刻（docker image inspect 的 .Created）。
    # 沒有它就答不出「那場是哪一版工具鏈」——2026-07-26 實際發生過 session 跑在 13 天前
    # 的 image 上而畫面完全看不出來。存 UTC（UtcDateTime 會把任何時區換算過去）。
    image_created_at: Mapped[_dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
    views: Mapped[list[View]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SessionHistory(Base):
    """已結束 session 的永久紀錄（ADR 0010）。

    `sessions` 是「當下狀態」：它要被對帳、要算配額、要跟 dockerd 比對，所以結束的列
    必須離開它。但「誰在什麼時候、用什麼 profile 開過什麼」不該跟著 container 一起消失
    ——結束時把快照搬到這裡，永久保留。

    刻意是**快照**而非外鍵關聯：使用者名稱、profile 都存當下的值，帳號日後被刪或改名，
    歷史仍讀得出來（user_id 為 SET NULL，只作為「還在的話是誰」的線索）。
    """

    __tablename__ = "session_history"
    # 一個 session 只會結束一次。沒有這條約束時，web worker 的 list() 對帳與 reconciler
    # 同時歸檔同一筆，兩邊都會先讀到列再各寫一筆，歷史就多出重複紀錄（且結束原因取決於
    # 誰先寫）。archive() 另以 BEGIN IMMEDIATE 序列化，這裡是 DB 層的兜底。
    __table_args__ = (UniqueConstraint("session_id", name="uq_history_session"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    container_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 帳號被刪也要留得住紀錄：user_id 可為 NULL，username 是當下的快照
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 與 sessions.profile 同一套（見那裡的說明）：對上層是 dict
    profile: Mapped[dict] = mapped_column(
        "profile_json", JSON(), nullable=False, default=dict)
    workdir: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[_dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    last_active_at: Mapped[_dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    # 就緒時刻的快照（見 Session.ready_at）。留著才算得出「這場的啟動花了多久」——
    # 歷史紀錄的用途正是回頭比較，只存結束時間是不夠的。
    ready_at: Mapped[_dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # 啟動當下的 CLI 版本與 image 打包時刻（快照，見 Session 上同名欄位）。
    # 歷史紀錄的用途就是回頭比較，這兩個是「那場的環境長什麼樣」最基本的兩個座標。
    cli_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_created_at: Mapped[_dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    ended_at: Mapped[_dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True)
    # terminated（使用者明確終止）/ exited（自行結束）/ gone（container 從外部消失）
    # / idle（閒置逾時被 reconciler 回收，見 reconciler 的 END_IDLE）
    ended_reason: Mapped[str] = mapped_column(String(16), nullable=False)
    # 誰按的終止。admin 終止得了別人的 session，而本系統不做租戶隔離——沒有這個欄位，
    # 「我的 session 為什麼不見了」就沒有任何線索。
    # NULL 代表不是人為終止（exited / gone / idle），或是舊紀錄。
    # 與 user_id 同一套做法：外鍵給關聯、username 給快照，帳號被刪也讀得出當時是誰。
    ended_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ended_by_username: Mapped[str | None] = mapped_column(String(64), nullable=True)


class View(Base):
    """一次「開網頁看終端」的暫態記錄（ADR 0008 on-demand ttyd）。

    port 的 UNIQUE 就是跨 worker 的分配仲裁：候選 port INSERT 撞約束即代表被佔、換下一個。
    ttyd 以 `-q` 起，關掉網頁即自行退出；殘留記錄由 reconciler 依 pid 存活檢查清理。
    """

    __tablename__ = "views"
    # port 唯一＝跨 worker 的 port 仲裁；session_id 唯一＝一個 session 只有一個 view。
    # 只有前者時，兩個 worker 可各自搶到不同 port、為同一 session 起兩個 ttyd，其中一個
    # 永遠等不到 client（`-q` 不會觸發）而長生不死（review H1）。
    __table_args__ = (
        UniqueConstraint("port", name="uq_views_port"),
        UniqueConstraint("session_id", name="uq_views_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 這個 view 實際是用哪一顆 ttyd 起的（config.TTYD_BINS 的 key）。
    # ⚠ **不可以**改用「這個人現在的偏好」去推：偏好改了不會換掉已經在跑的 ttyd
    #   （見 app.set_prefs 的註解），那時推出來的答案剛好在最需要它的時候是錯的。
    # nullable：既有列沒有這個值（輕量升級只加欄位，見 db._add_missing_columns），
    # 而「不知道」本來就是一個誠實的答案——畫面上不顯示標記即可。
    ttyd_bin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    session: Mapped[Session] = relationship(back_populates="views")


class Lease(Base):
    """互斥租約：讓「只該有一個執行者」的工作真的只有一個（review M2）。

    reconciler 做的是破壞性清理（force-remove container、刪登錄）。原本「只跑一份」
    只是部署慣例——滾動更新、重複的 service unit、或人為誤啟都會同時跑兩份。
    以 DB 這個唯一仲裁者持租約，才是真的強制。
    """

    __tablename__ = "leases"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[_dt.datetime] = mapped_column(UtcDateTime, nullable=False)

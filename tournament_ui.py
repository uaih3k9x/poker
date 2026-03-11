"""
tournament_ui.py

Tournament UI runner — round-robin tournament across many 1v1 poker bots.

Features:
- Round-robin (each distinct pairing plays N matches).
- Live standings table (wins / losses / win %).
- Play / Pause / Step controls.
- Configurable "Step batch" (run next N matches when stepping).
- Optional avatar images per bot (bot class attribute `image_path` or ./images/<sanitised_name>.png).
- Single background worker thread to run matches sequentially (faster & safer).
- UI update batching (only refresh table every M results to reduce redraw overhead).

Drop this next to main.py and the existing project files. It relies on your project's
`logic.Game`, `logic.Player`, and any example bots (RandomPlayer, RockyPlayer).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import importlib.util
import inspect
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, TextIO, Tuple

from logic import Game, Player, RandomPlayer, RockyPlayer

BOTS_DIR = Path(__file__).with_name('bots')
IMAGES_DIR = Path(__file__).with_name('images')
LOGS_DIR = Path(__file__).with_name('tournament_logs')

DEFAULT_MATCHES_PER_PAIR = 10
DEFAULT_DELAY_MS = 250
DEFAULT_SHUFFLE_MATCHES = True
DEFAULT_INCLUDE_BUILTINS = False
DEFAULT_STEP_BATCH = 1
DEFAULT_UI_UPDATE_EVERY = 10


@dataclass(frozen=True)
class BotSpec:
    name: str
    cls: type[Player]
    image_path: Optional[str] = None


@dataclass
class Stats:
    wins: int = 0
    losses: int = 0

    @property
    def played(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.played if self.played else 0.0


@dataclass(frozen=True)
class MatchTask:
    a: BotSpec
    b: BotSpec
    series_index: int
    series_total: int


@dataclass(frozen=True)
class MatchResult:
    duration_ms: int
    started_at: str
    finished_at: str
    winner: Optional[str] = None
    loser: Optional[str] = None
    error: Optional[str] = None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def _safe_module_name(file: Path) -> str:
    return f"bot_{file.stem}_{abs(hash(str(file)))}"


def _infer_image_path(bot_cls: type[Player]) -> Optional[str]:
    p = getattr(bot_cls, 'image_path', None)
    if isinstance(p, str) and p.strip():
        return p

    name = getattr(bot_cls, 'name', bot_cls.__name__)
    safe = ''.join(ch if ch.isalnum() else '_' for ch in str(name)).strip('_')
    for ext in ('.png', '.gif'):
        candidate = IMAGES_DIR / f"{safe}{ext}"
        if candidate.exists():
            return str(candidate)
    return None


def load_bots(bots_dir: Path, include_builtins: bool) -> List[BotSpec]:
    bots: List[BotSpec] = []

    if include_builtins:
        bots.append(BotSpec(RandomPlayer.name, RandomPlayer, _infer_image_path(RandomPlayer)))
        bots.append(BotSpec(RockyPlayer.name, RockyPlayer, _infer_image_path(RockyPlayer)))

    if not bots_dir.exists():
        bots_dir.mkdir(parents=True, exist_ok=True)
        return bots

    for file in sorted(bots_dir.glob('*.py')):
        if file.name.startswith('_'):
            continue

        module_name = _safe_module_name(file)
        spec = importlib.util.spec_from_file_location(module_name, file)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
        except Exception as e:
            print(f"Failed to import {file.name}: {e}")
            continue

        for _, obj in module.__dict__.items():
            if not inspect.isclass(obj):
                continue
            if obj.__module__ != module_name:
                continue
            if not issubclass(obj, Player) or obj is Player:
                continue

            name = getattr(obj, 'name', obj.__name__)
            image_path = _infer_image_path(obj)
            bots.append(BotSpec(str(name), obj, image_path))

    seen = set()
    unique: List[BotSpec] = []
    for b in bots:
        if b.name in seen:
            continue
        seen.add(b.name)
        unique.append(b)
    return unique


def build_round_robin(bots: List[BotSpec], matches_per_pair: int, shuffle: bool) -> List[MatchTask]:
    tasks: List[MatchTask] = []
    for i in range(len(bots)):
        for j in range(i + 1, len(bots)):
            for k in range(matches_per_pair):
                tasks.append(MatchTask(bots[i], bots[j], k + 1, matches_per_pair))
    if shuffle:
        random.shuffle(tasks)
    return tasks


def play_match(bot_a: BotSpec, bot_b: BotSpec) -> Tuple[str, str]:
    p1, p2 = bot_a.cls(), bot_b.cls()
    game = Game(p1, p2, debug=False)
    winner = game.simulate_hands()
    if winner is p1:
        return bot_a.name, bot_b.name
    return bot_b.name, bot_a.name


class TournamentUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('Poker Bot Tournament')
        self.root.geometry('980x640')
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        self._result_queue: "queue.Queue[Tuple[MatchTask, MatchResult]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_stop = threading.Event()
        self._worker_lock = threading.Lock()

        self._running = False
        self._pending_tasks: List[MatchTask] = []
        self._completed = 0
        self._total_scheduled = 0
        self._bots: List[BotSpec] = []
        self._stats: dict[str, Stats] = {}
        self._matchup_stats: dict[str, dict[str, Stats]] = {}
        self._expanded_bots: set[str] = set()
        self._log_file: Optional[TextIO] = None
        self._log_path: Optional[Path] = None
        self._run_id: Optional[str] = None

        self._avatar_cache: dict[str, tk.PhotoImage] = {}
        self._left_avatar: Optional[tk.PhotoImage] = None
        self._right_avatar: Optional[tk.PhotoImage] = None

        self._build_layout()
        self._reset_tournament()
        self._poll_results()

    def _build_layout(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        controls = ttk.Frame(top)
        controls.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.play_btn = ttk.Button(controls, text='Play', command=self._toggle_play)
        self.play_btn.grid(row=0, column=0, padx=(0, 6))

        self.step_btn = ttk.Button(controls, text='Step', command=self._step_once)
        self.step_btn.grid(row=0, column=1, padx=(0, 6))

        self.reset_btn = ttk.Button(controls, text='Reset', command=self._reset_tournament)
        self.reset_btn.grid(row=0, column=2, padx=(0, 12))

        ttk.Label(controls, text='Matches per pairing:').grid(row=0, column=3, sticky=tk.W)
        self.matches_var = tk.IntVar(value=DEFAULT_MATCHES_PER_PAIR)
        self.matches_spin = ttk.Spinbox(controls, from_=1, to=1000, width=6, textvariable=self.matches_var)
        self.matches_spin.grid(row=0, column=4, padx=(6, 12))

        ttk.Label(controls, text='Delay (ms):').grid(row=0, column=5, sticky=tk.W)

        self.delay_label = ttk.Label(controls, text=str(DEFAULT_DELAY_MS))
        self.delay_label.grid(row=0, column=7, sticky=tk.W)

        self.delay_scale = ttk.Scale(
            controls,
            from_=0,
            to=2000,
            orient=tk.HORIZONTAL,
            command=self._on_delay_scale,
        )
        self.delay_scale.grid(row=0, column=6, padx=(6, 6), sticky=tk.EW)
        self.delay_scale.set(DEFAULT_DELAY_MS)

        ttk.Label(controls, text='Step batch:').grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.step_batch_var = tk.IntVar(value=DEFAULT_STEP_BATCH)
        self.step_batch_spin = ttk.Spinbox(controls, from_=1, to=1000, width=6, textvariable=self.step_batch_var)
        self.step_batch_spin.grid(row=1, column=1, padx=(6, 12), pady=(8, 0))

        ttk.Label(controls, text='UI update every:').grid(row=1, column=2, sticky=tk.W, pady=(8, 0))
        self.update_every_var = tk.IntVar(value=DEFAULT_UI_UPDATE_EVERY)
        self.update_every_spin = ttk.Spinbox(controls, from_=1, to=1000, width=6, textvariable=self.update_every_var)
        self.update_every_spin.grid(row=1, column=3, padx=(6, 12), pady=(8, 0))

        self.shuffle_var = tk.BooleanVar(value=DEFAULT_SHUFFLE_MATCHES)
        self.shuffle_cb = ttk.Checkbutton(controls, text='Shuffle match order', variable=self.shuffle_var)
        self.shuffle_cb.grid(row=1, column=4, columnspan=2, sticky=tk.W, pady=(8, 0))

        self.builtins_var = tk.BooleanVar(value=DEFAULT_INCLUDE_BUILTINS)
        self.builtins_cb = ttk.Checkbutton(controls, text='Include Rocky/Rando', variable=self.builtins_var)
        self.builtins_cb.grid(row=1, column=6, columnspan=2, sticky=tk.W, pady=(8, 0))

        controls.columnconfigure(6, weight=1)

        now = ttk.Frame(top)
        now.pack(side=tk.RIGHT, fill=tk.Y)

        self.match_title = ttk.Label(now, text='Ready', font=('TkDefaultFont', 12, 'bold'))
        self.match_title.pack(anchor=tk.E)
        self.match_progress = ttk.Label(now, text='')
        self.match_progress.pack(anchor=tk.E)

        mid = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        avatars = ttk.Frame(mid)
        avatars.pack(side=tk.TOP, fill=tk.X)

        self.left_img = ttk.Label(avatars)
        self.left_img.pack(side=tk.LEFT)
        self.left_name = ttk.Label(avatars, text='', font=('TkDefaultFont', 11, 'bold'))
        self.left_name.pack(side=tk.LEFT, padx=(8, 24))

        self.right_name = ttk.Label(avatars, text='', font=('TkDefaultFont', 11, 'bold'))
        self.right_name.pack(side=tk.RIGHT, padx=(24, 8))
        self.right_img = ttk.Label(avatars)
        self.right_img.pack(side=tk.RIGHT)

        table_frame = ttk.Frame(mid)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))

        columns = ('rank', 'played', 'wins', 'losses', 'winrate')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='tree headings', height=18)
        self.tree.heading('#0', text='Bot / Matchup')
        self.tree.heading('rank', text='#')
        self.tree.heading('played', text='Played')
        self.tree.heading('wins', text='Wins')
        self.tree.heading('losses', text='Losses')
        self.tree.heading('winrate', text='Win %')

        self.tree.column('#0', width=320, anchor=tk.W)
        self.tree.column('rank', width=40, anchor=tk.E)
        self.tree.column('played', width=80, anchor=tk.E)
        self.tree.column('wins', width=80, anchor=tk.E)
        self.tree.column('losses', width=80, anchor=tk.E)
        self.tree.column('winrate', width=90, anchor=tk.E)
        self.tree.bind('<<TreeviewOpen>>', self._on_tree_open)
        self.tree.bind('<<TreeviewClose>>', self._on_tree_close)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        footer = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.status = ttk.Label(footer, text='')
        self.status.pack(side=tk.LEFT)

    def _on_delay_scale(self, value: str) -> None:
        try:
            v = int(float(value))
        except (TypeError, ValueError):
            return
        self.delay_label.config(text=str(v))

    def _on_close(self) -> None:
        self._running = False
        self._worker_stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=0.5)
        self._drain_result_queue(refresh_ui=False)
        self._finalize_log(reason='closed')
        self.root.destroy()

    def _start_log(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self._log_path = LOGS_DIR / f'tournament_{self._run_id}.jsonl'
        self._log_file = self._log_path.open('a', encoding='utf-8')
        self._log_event(
            'tournament_started',
            run_id=self._run_id,
            scheduled_matches=self._total_scheduled,
            bots=[{'name': b.name, 'image_path': b.image_path} for b in self._bots],
            settings={
                'matches_per_pair': int(self.matches_var.get()),
                'delay_ms': int(float(self.delay_scale.get())),
                'shuffle_matches': bool(self.shuffle_var.get()),
                'include_builtins': bool(self.builtins_var.get()),
                'step_batch': max(1, int(self.step_batch_var.get())),
                'ui_update_every': max(1, int(self.update_every_var.get())),
            },
        )

    def _log_event(self, event: str, **payload: object) -> None:
        if self._log_file is None:
            return
        record = {
            'ts': _now_iso(),
            'event': event,
            **payload,
        }
        json.dump(record, self._log_file, ensure_ascii=False)
        self._log_file.write('\n')
        self._log_file.flush()

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
        self._log_file = None
        self._log_path = None
        self._run_id = None

    def _stats_snapshot(self, name: str, stats: Stats) -> dict[str, object]:
        return {
            'name': name,
            'played': stats.played,
            'wins': stats.wins,
            'losses': stats.losses,
            'win_rate': round(stats.win_rate, 6),
            'win_pct': round(stats.win_rate * 100, 2),
        }

    def _matchup_snapshot(self, opponent: str, stats: Stats) -> dict[str, object]:
        return {
            'opponent': opponent,
            'played': stats.played,
            'wins': stats.wins,
            'losses': stats.losses,
            'win_rate': round(stats.win_rate, 6),
            'win_pct': round(stats.win_rate * 100, 2),
        }

    def _standings_snapshot(self) -> List[dict[str, object]]:
        ordered = sorted(
            self._stats.items(),
            key=lambda kv: (kv[1].wins, kv[1].win_rate, kv[0]),
            reverse=True,
        )
        return [
            {
                'rank': idx,
                **self._stats_snapshot(name, stats),
            }
            for idx, (name, stats) in enumerate(ordered, start=1)
        ]

    def _matchups_snapshot(self) -> dict[str, List[dict[str, object]]]:
        return {
            bot_name: [
                self._matchup_snapshot(opponent, stats)
                for opponent, stats in sorted(opponents.items())
            ]
            for bot_name, opponents in sorted(self._matchup_stats.items())
        }

    def _finalize_log(self, reason: str) -> None:
        if self._log_file is None:
            return
        event = 'tournament_finished' if reason == 'finished' else 'tournament_interrupted'
        self._log_event(
            event,
            run_id=self._run_id,
            reason=reason,
            completed_matches=self._completed,
            scheduled_matches=self._total_scheduled,
            remaining_matches=max(0, self._total_scheduled - self._completed),
            leader=self._leader_name(),
            standings=self._standings_snapshot(),
            matchups=self._matchups_snapshot(),
        )
        self._close_log()

    def _consume_result(self, task: MatchTask, result: MatchResult) -> None:
        self._completed += 1
        common_payload = {
            'run_id': self._run_id,
            'match_index': self._completed,
            'scheduled_matches': self._total_scheduled,
            'remaining_matches': max(0, self._total_scheduled - self._completed),
            'bot_a': task.a.name,
            'bot_b': task.b.name,
            'series_index': task.series_index,
            'series_total': task.series_total,
            'started_at': result.started_at,
            'finished_at': result.finished_at,
            'duration_ms': result.duration_ms,
        }

        if result.error is not None:
            self._stats[task.a.name].losses += 1
            self._stats[task.b.name].losses += 1
            self._matchup_stats[task.a.name][task.b.name].losses += 1
            self._matchup_stats[task.b.name][task.a.name].losses += 1
            self.status.config(text=f'Match error ({task.a.name} vs {task.b.name}): {result.error}')
            self._log_event(
                'match_error',
                **common_payload,
                error=result.error,
            )
            return

        winner = result.winner or ''
        loser = result.loser or ''
        self._stats[winner].wins += 1
        self._stats[loser].losses += 1
        self._matchup_stats[winner][loser].wins += 1
        self._matchup_stats[loser][winner].losses += 1
        self._log_event(
            'match_completed',
            **common_payload,
            winner=winner,
            loser=loser,
            leader=self._leader_name(),
            winner_totals=self._stats_snapshot(winner, self._stats[winner]),
            loser_totals=self._stats_snapshot(loser, self._stats[loser]),
            winner_vs_loser=self._matchup_snapshot(loser, self._matchup_stats[winner][loser]),
            loser_vs_winner=self._matchup_snapshot(winner, self._matchup_stats[loser][winner]),
        )

    def _drain_result_queue(self, refresh_ui: bool) -> bool:
        updated = False
        update_every = max(1, int(self.update_every_var.get()))
        local_count = 0

        try:
            while True:
                task, result = self._result_queue.get_nowait()
                self._consume_result(task, result)
                updated = True
                local_count += 1

                if refresh_ui and local_count >= update_every:
                    self._refresh_table()
                    self._update_status_line(final=False)
                    local_count = 0
        except queue.Empty:
            pass

        if refresh_ui and updated and local_count > 0:
            self._refresh_table()
            self._update_status_line(final=False)
        return updated

    def _reset_tournament(self) -> None:
        self._running = False
        self.play_btn.config(text='Play')

        self._worker_stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=0.2)
        self._drain_result_queue(refresh_ui=False)
        self._finalize_log(reason='reset')

        self._bots = load_bots(BOTS_DIR, include_builtins=self.builtins_var.get())
        if len(self._bots) < 2:
            self._pending_tasks = []
            self._stats = {}
            self._matchup_stats = {}
            self._total_scheduled = 0
            self._expanded_bots.clear()
            self._refresh_table()
            self.status.config(text=f'Found {len(self._bots)} bot(s). Add at least 2 into {BOTS_DIR}.')
            self.match_title.config(text='Waiting for bots…')
            return

        self._stats = {b.name: Stats() for b in self._bots}
        self._matchup_stats = {
            bot.name: {
                opponent.name: Stats()
                for opponent in self._bots
                if opponent.name != bot.name
            }
            for bot in self._bots
        }
        matches_per_pair = int(self.matches_var.get())
        shuffle = bool(self.shuffle_var.get())
        self._pending_tasks = build_round_robin(self._bots, matches_per_pair, shuffle)
        self._completed = 0
        self._total_scheduled = len(self._pending_tasks)
        self._start_log()

        self._avatar_cache.clear()
        self._left_avatar = None
        self._right_avatar = None
        self._expanded_bots.clear()
        self._refresh_table()
        self._set_current_match(None)
        self._update_status_line(final=False)

    def _start_worker(self, batch_size: Optional[int]) -> None:
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return

            self._worker_stop.clear()

            def _worker_loop() -> None:
                processed = 0
                while not self._worker_stop.is_set():
                    if not self._pending_tasks:
                        break

                    task = self._pending_tasks.pop(0)
                    self.root.after(0, lambda t=task: self._set_current_match(t))
                    started_at = _now_iso()
                    started_clock = time.perf_counter()

                    try:
                        winner, loser = play_match(task.a, task.b)
                        self._result_queue.put((
                            task,
                            MatchResult(
                                winner=winner,
                                loser=loser,
                                duration_ms=int((time.perf_counter() - started_clock) * 1000),
                                started_at=started_at,
                                finished_at=_now_iso(),
                            ),
                        ))
                    except Exception as e:
                        self._result_queue.put((
                            task,
                            MatchResult(
                                error=f'{type(e).__name__}: {e}',
                                duration_ms=int((time.perf_counter() - started_clock) * 1000),
                                started_at=started_at,
                                finished_at=_now_iso(),
                            ),
                        ))

                    processed += 1

                    delay_ms = int(float(self.delay_scale.get()))
                    if delay_ms > 0:
                        time.sleep(delay_ms / 1000.0)

                    if batch_size is not None and processed >= batch_size:
                        break

                self._worker_stop.set()
                self.root.after(0, lambda: self._set_current_match(None))

            self._worker = threading.Thread(target=_worker_loop, daemon=True)
            self._worker.start()

    def _toggle_play(self) -> None:
        if not self._pending_tasks:
            return

        self._running = not self._running
        self.play_btn.config(text='Pause' if self._running else 'Play')

        if self._running:
            self._start_worker(batch_size=None)
        else:
            self._worker_stop.set()

    def _step_once(self) -> None:
        if self._running or not self._pending_tasks:
            return
        batch = max(1, int(self.step_batch_var.get()))
        self._start_worker(batch_size=batch)

    def _poll_results(self) -> None:
        updated = self._drain_result_queue(refresh_ui=True)

        if not self._pending_tasks and self._worker and not self._worker.is_alive():
            self._finish()

        self.root.after(100, self._poll_results)

    def _finish(self) -> None:
        self._running = False
        self.play_btn.config(text='Play')
        self._set_current_match(None)
        self._update_status_line(final=True)
        self._finalize_log(reason='finished')

    def _refresh_table(self) -> None:
        open_states: dict[str, bool] = {}
        for row in self.tree.get_children():
            name = self.tree.item(row, 'text')
            if name:
                open_states[name] = bool(self.tree.item(row, 'open'))

        for row in self.tree.get_children():
            self.tree.delete(row)

        ordered = sorted(
            self._stats.items(),
            key=lambda kv: (kv[1].wins, kv[1].win_rate, kv[0]),
            reverse=True,
        )
        for idx, (name, s) in enumerate(ordered, start=1):
            is_open = open_states.get(name, name in self._expanded_bots)
            parent = self.tree.insert(
                '',
                tk.END,
                text=name,
                open=is_open,
                values=(
                    idx,
                    s.played,
                    s.wins,
                    s.losses,
                    f"{s.win_rate * 100:.2f}",
                ),
            )
            for opponent, matchup in sorted(self._matchup_stats.get(name, {}).items()):
                self.tree.insert(
                    parent,
                    tk.END,
                    text=f'vs {opponent}',
                    values=(
                        '',
                        matchup.played,
                        matchup.wins,
                        matchup.losses,
                        f"{matchup.win_rate * 100:.2f}",
                    ),
                )

    def _on_tree_open(self, _event: tk.Event) -> None:
        item = self.tree.focus()
        if not item or self.tree.parent(item):
            return
        name = self.tree.item(item, 'text')
        if name:
            self._expanded_bots.add(name)

    def _on_tree_close(self, _event: tk.Event) -> None:
        item = self.tree.focus()
        if not item or self.tree.parent(item):
            return
        name = self.tree.item(item, 'text')
        if name:
            self._expanded_bots.discard(name)

    def _set_current_match(self, task: Optional[MatchTask]) -> None:
        if task is None:
            self.match_title.config(text='Ready' if self._pending_tasks else 'Finished')
            self.match_progress.config(text='')
            self.left_name.config(text='')
            self.right_name.config(text='')
            self.left_img.config(image='')
            self.right_img.config(image='')
            self._left_avatar = None
            self._right_avatar = None
            return

        self.match_title.config(text=f'{task.a.name} vs {task.b.name}')
        self.match_progress.config(text=f'Series match {task.series_index}/{task.series_total}')
        self.left_name.config(text=task.a.name)
        self.right_name.config(text=task.b.name)

        self._left_avatar = self._get_avatar(task.a)
        self._right_avatar = self._get_avatar(task.b)
        self.left_img.config(image=self._left_avatar if self._left_avatar else '')
        self.right_img.config(image=self._right_avatar if self._right_avatar else '')

    def _get_avatar(self, bot: BotSpec) -> Optional[tk.PhotoImage]:
        if not bot.image_path:
            return None

        try:
            key = str(Path(bot.image_path).expanduser().resolve())
        except Exception:
            key = bot.image_path

        cached = self._avatar_cache.get(key)
        if cached is not None:
            return cached

        img = self._load_image(key)
        if img is not None:
            self._avatar_cache[key] = img
        return img

    def _load_image(self, path: str, max_size: int = 96) -> Optional[tk.PhotoImage]:
        try:
            from PIL import Image, ImageTk  # type: ignore
            im = Image.open(path)
            im.thumbnail((max_size, max_size))
            return ImageTk.PhotoImage(im)
        except Exception:
            pass

        try:
            img = tk.PhotoImage(file=path)
            w, h = img.width(), img.height()
            scale = max(1, int(max(w, h) / max_size))
            if scale > 1:
                img = img.subsample(scale, scale)
            return img
        except Exception:
            return None

    def _update_status_line(self, final: bool) -> None:
        total = self._total_scheduled
        if total == 0:
            self.status.config(text='')
            return

        leader = self._leader_name()
        if final:
            self.status.config(text=f'Finished: {self._completed}/{total} matches. Winner: {leader}')
        else:
            self.status.config(text=f'Progress: {self._completed}/{total} matches. Current leader: {leader}')

    def _leader_name(self) -> str:
        if not self._stats:
            return ''
        return max(self._stats.items(), key=lambda kv: (kv[1].wins, kv[1].win_rate))[0]


def main() -> None:
    root = tk.Tk()
    TournamentUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()

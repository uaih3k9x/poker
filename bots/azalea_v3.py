from collections import Counter
from multiprocessing import Pool, cpu_count
import random

from phevaluator.evaluator import evaluate_cards

from logic import Game, Move, Player, RandomPlayer


MATCHES = 1000
PARALLEL = False

BIG_BLIND = 100
RANK_VALUES = {rank: value for value, rank in enumerate("23456789TJQKA", start=2)}
AGGRESSIVE_MOVES = {Move.BET, Move.RAISE, Move.ALL_IN}
ALL_CARDS = tuple(rank + suit for suit in "dhsc" for rank in "23456789TJQKA")


class MyPlayer(Player):
    name = "AzaleaHUD"
    image_path = "images/your_image.png"

    def __init__(self) -> None:
        super().__init__()
        self._stats: Counter[str] = Counter()
        self._last_cards: tuple[str, ...] = ()
        self._last_street = -1
        self._last_history_len = 0
        self._street_marks: set[str] = set()
        self._on_button = False
        self._preflop_aggressor_me: bool | None = None

    @staticmethod
    def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def ranks(cards: list[str]) -> list[int]:
        return [RANK_VALUES[card[0]] for card in cards]

    @staticmethod
    def preflop_strength_for(cards: list[str]) -> float:
        r1, r2 = (RANK_VALUES[c[0]] for c in cards)
        suited = cards[0][1] == cards[1][1]
        high, low = max(r1, r2), min(r1, r2)
        gap = high - low

        if r1 == r2:
            return MyPlayer.clamp(0.4 + high * 0.04, 0.48, 0.96)

        score = 0.16 + high * 0.027 + low * 0.014
        if suited:
            score += 0.05
        if gap <= 1:
            score += 0.04
        elif gap == 2:
            score += 0.02
        elif gap >= 5:
            score -= 0.05
        if low >= 10:
            score += 0.04
        if high == 14 and low >= 5:
            score += 0.03

        return MyPlayer.clamp(score, 0.23, 0.88)

    def preflop_strength(self) -> float:
        return self.preflop_strength_for(self.cards)

    def rate(self, made_key: str, seen_key: str, default: float, min_seen: int = 5) -> float:
        seen = self._stats[seen_key]
        if seen < min_seen:
            return default
        return self._stats[made_key] / seen

    def opponent_profile(self) -> dict[str, float | bool]:
        if len(self.hands_shown) >= 3:
            average_strength = sum(
                self.preflop_strength_for(cards)
                for cards, _ in self.hands_shown
            ) / len(self.hands_shown)
        else:
            average_strength = 0.56

        return {
            "tight": average_strength >= 0.68,
            "loose": average_strength <= 0.48,
            "button_raise_rate": self.rate("opp_button_raise", "opp_button_open_seen", 0.48),
            "button_limp_rate": self.rate("opp_button_limp", "opp_button_open_seen", 0.24),
            "bb_3bet_rate": self.rate("opp_bb_3bet", "opp_bb_defend_seen", 0.16),
            "flop_first_bet_rate": self.rate("opp_first_bet_3", "opp_first_seen_3", 0.48),
            "turn_first_bet_rate": self.rate("opp_first_bet_4", "opp_first_seen_4", 0.45),
            "river_first_bet_rate": self.rate("opp_first_bet_5", "opp_first_seen_5", 0.42),
            "flop_probe_rate": self.rate("opp_probe_bet_3", "opp_probe_seen_3", 0.46),
            "turn_probe_rate": self.rate("opp_probe_bet_4", "opp_probe_seen_4", 0.42),
            "river_probe_rate": self.rate("opp_probe_bet_5", "opp_probe_seen_5", 0.38),
        }

    def commitments(self, round_history: list[tuple[Move, int]]) -> tuple[int, int]:
        my_commitment = 0
        opp_commitment = 0
        actor_is_me = len(round_history) % 2 == 0

        for move, amount in round_history:
            if move in (Move.BET, Move.RAISE, Move.CALL, Move.ALL_IN):
                if actor_is_me:
                    my_commitment = amount
                else:
                    opp_commitment = amount
            actor_is_me = not actor_is_me

        return my_commitment, opp_commitment

    def estimate_pot(self, community_cards: list[str], my_commitment: int, opp_commitment: int) -> int:
        baseline = 0 if not community_cards else 200 + max(0, len(community_cards) - 3) * 50
        return baseline + my_commitment + opp_commitment

    def board_texture(self, community_cards: list[str]) -> float:
        if len(community_cards) < 3:
            return 0.0

        ranks = sorted(set(self.ranks(community_cards)))
        suit_counts = Counter(card[1] for card in community_cards)
        wetness = 0.0

        max_suit = max(suit_counts.values(), default=0)
        if max_suit >= 3:
            wetness += 0.08
        elif max_suit == 2:
            wetness += 0.03

        if len(ranks) >= 3:
            span = ranks[-1] - ranks[0]
            if span <= 4:
                wetness += 0.08
            elif span <= 6:
                wetness += 0.04

            wetness += 0.03 * sum(
                second - first <= 2 for first, second in zip(ranks, ranks[1:])
            )

        if len(ranks) < len(community_cards):
            wetness -= 0.03

        return self.clamp(wetness, 0.0, 0.22)

    def straight_draw_flags(self, cards: list[str]) -> tuple[bool, bool]:
        rank_set = set(self.ranks(cards))
        if 14 in rank_set:
            rank_set.add(1)

        open_ended = False
        gutshot = False
        for start in range(1, 11):
            needed = set(range(start, start + 5))
            hits = needed & rank_set
            if len(hits) != 4:
                continue

            missing = next(iter(needed - hits))
            if missing in (start, start + 4):
                open_ended = True
            else:
                gutshot = True

        return open_ended, gutshot

    def postflop_strength(self, community_cards: list[str]) -> tuple[float, bool]:
        all_cards = community_cards + self.cards
        suit_counts = Counter(card[1] for card in all_cards)
        flush_draw = len(community_cards) < 5 and max(suit_counts.values(), default=0) >= 4
        open_ended, gutshot = self.straight_draw_flags(all_cards)
        strong_draw = flush_draw or open_ended

        known = set(self.cards + community_cards)
        remaining = tuple(card for card in ALL_CARDS if card not in known)
        cards_needed = 5 - len(community_cards)
        samples = {3: 96, 4: 72, 5: 48}[len(community_cards)]

        wins = 0.0
        for _ in range(samples):
            draw = random.sample(remaining, 2 + cards_needed)
            opp_cards = draw[:2]
            full_board = community_cards + draw[2:]

            my_rank = evaluate_cards(*full_board, *self.cards)
            opp_rank = evaluate_cards(*full_board, *opp_cards)
            if my_rank < opp_rank:
                wins += 1.0
            elif my_rank == opp_rank:
                wins += 0.5

        strength = wins / samples
        if strong_draw and gutshot:
            strength += 0.02

        return self.clamp(strength), strong_draw

    def aggressive_action(
        self,
        valid_moves: list[Move],
        min_bet: int,
        max_bet: int,
        target: int,
        fallback: Move,
    ) -> tuple[Move, int] | Move:
        target = max(min_bet, min(max_bet, target))
        if target >= max_bet and Move.ALL_IN in valid_moves:
            return Move.ALL_IN
        if Move.RAISE in valid_moves:
            return (Move.RAISE, target)
        if Move.BET in valid_moves:
            return (Move.BET, target)
        if fallback in valid_moves:
            return fallback
        if Move.CHECK in valid_moves:
            return Move.CHECK
        return Move.FOLD

    def reset_hand_state(self) -> None:
        self._street_marks = set()
        self._preflop_aggressor_me = None

    def sync_state(self, community_cards: list[str], round_history: list[tuple[Move, int]]) -> None:
        street = len(community_cards)
        cards_key = tuple(self.cards)
        new_hand = (
            self._last_street == -1
            or cards_key != self._last_cards
            or street < self._last_street
            or (street == 0 and len(round_history) < self._last_history_len)
        )

        if new_hand:
            self.reset_hand_state()
            self._on_button = self.pot_commitment <= BIG_BLIND // 2

        if street != self._last_street:
            self._street_marks = set()

        self._last_cards = cards_key
        self._last_street = street
        self._last_history_len = len(round_history)

    def last_aggressor(self, community_cards: list[str], round_history: list[tuple[Move, int]]) -> bool | None:
        actor_is_me = len(round_history) % 2 == 0
        last_aggressor_is_me = None

        for index, (move, amount) in enumerate(round_history):
            blind_post = (
                not community_cards
                and index < 2
                and move == Move.BET
                and amount <= BIG_BLIND
            )
            if not blind_post and move in AGGRESSIVE_MOVES:
                last_aggressor_is_me = actor_is_me
            actor_is_me = not actor_is_me

        return last_aggressor_is_me

    def observe_opponent(self, community_cards: list[str], round_history: list[tuple[Move, int]]) -> None:
        street = len(community_cards)

        if street == 0:
            last_aggressor = self.last_aggressor(community_cards, round_history)
            if last_aggressor is not None:
                self._preflop_aggressor_me = last_aggressor

            if not self._on_button and len(round_history) >= 3 and "bb_preflop_seen" not in self._street_marks:
                self._stats["opp_button_open_seen"] += 1
                action = round_history[2][0]
                if action in (Move.RAISE, Move.ALL_IN):
                    self._stats["opp_button_raise"] += 1
                elif action == Move.CALL:
                    self._stats["opp_button_limp"] += 1
                self._street_marks.add("bb_preflop_seen")

            if self._on_button and len(round_history) >= 4 and "button_preflop_seen" not in self._street_marks:
                self._stats["opp_bb_defend_seen"] += 1
                response = round_history[3][0]
                if response in (Move.RAISE, Move.ALL_IN):
                    self._stats["opp_bb_3bet"] += 1
                self._street_marks.add("button_preflop_seen")
            return

        if self._on_button and len(round_history) >= 1:
            mark = f"first_seen_{street}"
            if mark not in self._street_marks:
                self._stats[f"opp_first_seen_{street}"] += 1
                if round_history[0][0] in AGGRESSIVE_MOVES:
                    self._stats[f"opp_first_bet_{street}"] += 1
                self._street_marks.add(mark)

        if not self._on_button and len(round_history) >= 2 and round_history[0][0] == Move.CHECK:
            mark = f"probe_seen_{street}"
            if mark not in self._street_marks:
                self._stats[f"opp_probe_seen_{street}"] += 1
                if round_history[1][0] in AGGRESSIVE_MOVES:
                    self._stats[f"opp_probe_bet_{street}"] += 1
                self._street_marks.add(mark)

    def street_bet_rate(self, street: int, profile: dict[str, float | bool], facing_after_check: bool) -> float:
        if facing_after_check:
            return float(profile[f"{('flop', 'turn', 'river')[street - 3]}_probe_rate"])
        return float(profile[f"{('flop', 'turn', 'river')[street - 3]}_first_bet_rate"])

    def preflop_move(
        self,
        valid_moves: list[Move],
        min_bet: int,
        max_bet: int,
        to_call: int,
        pot_odds: float,
        round_history: list[tuple[Move, int]],
        profile: dict[str, float | bool],
    ) -> tuple[Move, int] | Move:
        strength = self.preflop_strength()
        opp_tight = bool(profile["tight"])
        button_raise_rate = float(profile["button_raise_rate"])
        button_limp_rate = float(profile["button_limp_rate"])
        bb_3bet_rate = float(profile["bb_3bet_rate"])
        maniac = button_raise_rate >= 0.62 or bb_3bet_rate >= 0.26
        button_first_action = self._on_button and len(round_history) == 2

        if button_first_action:
            raise_threshold = 0.31
            if bb_3bet_rate <= 0.12:
                raise_threshold -= 0.03
            elif bb_3bet_rate >= 0.24:
                raise_threshold += 0.03

            limp_threshold = raise_threshold - 0.07

            if strength >= 0.84:
                return self.aggressive_action(valid_moves, min_bet, max_bet, 325, Move.CALL)
            if strength >= raise_threshold:
                return self.aggressive_action(valid_moves, min_bet, max_bet, 250, Move.CALL)
            if (bb_3bet_rate >= 0.2 or maniac) and strength >= limp_threshold and Move.CALL in valid_moves:
                return Move.CALL
            return Move.FOLD

        if to_call == 0:
            iso_threshold = 0.37
            if opp_tight:
                iso_threshold -= 0.03
            if button_limp_rate >= 0.3:
                iso_threshold -= 0.02

            if strength >= 0.84:
                return self.aggressive_action(valid_moves, min_bet, max_bet, 375, Move.CHECK)
            if strength >= iso_threshold:
                return self.aggressive_action(valid_moves, min_bet, max_bet, max(300, min_bet), Move.CHECK)
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        bet_size_bb = max(2.0, to_call / BIG_BLIND)
        stack_pressure = to_call / max(max_bet, 1)

        if not self._on_button:
            if strength >= 0.9 or (strength >= 0.82 and stack_pressure > 0.22):
                if Move.ALL_IN in valid_moves and max_bet <= 2200:
                    return Move.ALL_IN
                target = max(min_bet, int((to_call + self.pot_commitment) * 2.45))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            reraise_threshold = 0.72
            if button_raise_rate >= 0.58 and bet_size_bb <= 5.5:
                reraise_threshold -= 0.04
            elif button_raise_rate <= 0.4:
                reraise_threshold += 0.03
            if maniac and bet_size_bb <= 4.5:
                reraise_threshold -= 0.03

            if strength >= reraise_threshold and bet_size_bb <= 6 and Move.RAISE in valid_moves:
                target = max(min_bet, int((to_call + self.pot_commitment) * 2.25))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            defend_threshold = max(0.42, pot_odds + 0.11)
            if button_raise_rate >= 0.58 and to_call <= 250:
                defend_threshold -= 0.04
            elif button_raise_rate <= 0.4:
                defend_threshold += 0.03
            if maniac and to_call <= 350:
                defend_threshold -= 0.04

            defend_cap = max(950, int(max_bet * 0.26)) if maniac else max(750, int(max_bet * 0.2))
            if strength >= defend_threshold and to_call <= defend_cap and Move.CALL in valid_moves:
                return Move.CALL
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        if strength >= 0.9 or (strength >= 0.82 and stack_pressure > 0.22):
            if Move.ALL_IN in valid_moves and max_bet <= 2200:
                return Move.ALL_IN
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.35))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        if strength >= 0.74 and bet_size_bb <= 6 and Move.RAISE in valid_moves:
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.1))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        defend_threshold = max(0.43, pot_odds + 0.1)
        if bb_3bet_rate >= 0.24 and to_call <= 450:
            defend_threshold -= 0.03
        elif bb_3bet_rate <= 0.12:
            defend_threshold += 0.03
        if maniac and to_call <= 450:
            defend_threshold -= 0.03

        defend_cap = max(900, int(max_bet * 0.24)) if maniac else max(700, int(max_bet * 0.18))
        if strength >= defend_threshold and to_call <= defend_cap and Move.CALL in valid_moves:
            return Move.CALL
        return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

    def move(
        self,
        community_cards: list[str],
        valid_moves: list[Move],
        round_history: list[tuple[Move, int]],
        min_bet: int,
        max_bet: int,
    ) -> tuple[Move, int] | Move:
        self.sync_state(community_cards, round_history)
        self.observe_opponent(community_cards, round_history)

        my_commitment, opp_commitment = self.commitments(round_history)
        to_call = max(0, opp_commitment - my_commitment)
        estimated_pot = self.estimate_pot(community_cards, my_commitment, opp_commitment)
        pot_odds = to_call / (estimated_pot + to_call) if to_call > 0 else 0.0
        profile = self.opponent_profile()

        if not community_cards:
            action = self.preflop_move(valid_moves, min_bet, max_bet, to_call, pot_odds, round_history, profile)
            if action in AGGRESSIVE_MOVES or (isinstance(action, tuple) and action[0] in AGGRESSIVE_MOVES):
                self._preflop_aggressor_me = True
            return action

        strength, strong_draw = self.postflop_strength(community_cards)
        wetness = self.board_texture(community_cards)
        street = len(community_cards)
        aggression_count = sum(move in AGGRESSIVE_MOVES for move, _ in round_history)
        facing_heat = to_call > 0 and aggression_count >= 2
        facing_after_check = not self._on_button and len(round_history) >= 2 and round_history[0][0] == Move.CHECK
        street_bet_rate = self.street_bet_rate(street, profile, facing_after_check)
        opp_tight = bool(profile["tight"])
        opp_loose = bool(profile["loose"])
        button_raise_rate = float(profile["button_raise_rate"])
        bb_3bet_rate = float(profile["bb_3bet_rate"])
        checked_to_me = self._on_button and round_history and round_history[0][0] == Move.CHECK
        maniac = (
            (opp_loose and street_bet_rate >= 0.52)
            or button_raise_rate >= 0.62
            or bb_3bet_rate >= 0.26
        )
        chaotic = (
            (opp_loose and street_bet_rate >= 0.62)
            or button_raise_rate >= 0.7
            or bb_3bet_rate >= 0.34
        )

        if chaotic:
            if to_call > 0:
                if strength >= 0.74:
                    target = max(min_bet, int(opp_commitment + max(to_call * 1.5, estimated_pot * 0.72)))
                    return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)
                if strength >= 0.52 and Move.CALL in valid_moves:
                    return Move.CALL
                if strength >= 0.36 and to_call <= max(250, int(max_bet * 0.08)) and Move.CALL in valid_moves:
                    return Move.CALL
                return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

            if strength >= 0.68:
                target = max(min_bet, int(estimated_pot * 0.8))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)
            if strength >= 0.56:
                target = max(min_bet, int(estimated_pot * 0.62))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)
            if strong_draw and len(community_cards) < 5 and checked_to_me and strength >= 0.42:
                target = max(min_bet, int(estimated_pot * 0.45))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        if to_call > 0:
            if strength >= 0.79:
                target = max(
                    min_bet,
                    int(opp_commitment + max(to_call * 1.65, estimated_pot * (0.52 + wetness))),
                )
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            if (
                strong_draw
                and len(community_cards) < 5
                and street_bet_rate >= 0.56
                and not facing_heat
                and strength >= max(0.45, pot_odds - 0.02)
                and Move.RAISE in valid_moves
            ):
                target = max(
                    min_bet,
                    int(opp_commitment + max(to_call * 1.15, estimated_pot * (0.34 + wetness))),
                )
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            if strength >= 0.64 and not facing_heat and street_bet_rate >= 0.52 and Move.RAISE in valid_moves:
                target = max(
                    min_bet,
                    int(opp_commitment + max(to_call * 1.25, estimated_pot * (0.28 + wetness))),
                )
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            call_threshold = pot_odds + 0.06
            if opp_loose:
                call_threshold -= 0.03
            if opp_tight:
                call_threshold += 0.02
            if street_bet_rate >= 0.6:
                call_threshold -= 0.03
            elif street_bet_rate <= 0.36:
                call_threshold += 0.04
            if maniac:
                call_threshold -= 0.04
            if facing_heat:
                call_threshold += 0.05
            if strong_draw and len(community_cards) < 5:
                call_threshold -= 0.05
            if len(community_cards) == 5 and street_bet_rate <= 0.34 and not maniac:
                call_threshold += 0.03

            continue_cap = (
                max(1200, int(max_bet * 0.34))
                if maniac
                else max(900, int(max_bet * (0.26 if street_bet_rate >= 0.56 else 0.22)))
            )
            if strength >= call_threshold and to_call <= continue_cap and Move.CALL in valid_moves:
                return Move.CALL

            cheap_draw_cap = 350 if maniac else 300 if street_bet_rate >= 0.56 else 200
            if strong_draw and to_call <= cheap_draw_cap and Move.CALL in valid_moves:
                return Move.CALL

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        if strength >= 0.74:
            fraction = 0.84 if maniac else 0.72 + wetness * 0.25
            target = max(min_bet, int(estimated_pot * fraction))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if strength >= (0.54 if maniac else 0.59):
            fraction = 0.66 if maniac else 0.54 + wetness * 0.15
            target = max(min_bet, int(estimated_pot * fraction))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if (
            checked_to_me
            and street == 3
            and self._preflop_aggressor_me is True
            and strength >= 0.36
            and street_bet_rate <= 0.42
            and wetness <= 0.12
        ):
            target = max(min_bet, int(estimated_pot * 0.34))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if (
            checked_to_me
            and street_bet_rate <= 0.36
            and wetness <= 0.1
            and strength >= 0.42
            and not maniac
        ):
            target = max(min_bet, int(estimated_pot * 0.4))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if strong_draw and len(community_cards) < 5 and (checked_to_me or not opp_loose) and not maniac:
            target = max(min_bet, int(estimated_pot * (0.42 + wetness * 0.2)))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if Move.CHECK in valid_moves:
            return Move.CHECK
        return Move.FOLD


def run_match(_: int) -> str:
    p1, p2 = MyPlayer(), RandomPlayer()
    game = Game(p1, p2, debug=False)
    return game.simulate_hands().name


if __name__ == "__main__":
    win_counts = Counter()
    if PARALLEL:
        with Pool(cpu_count()) as pool:
            results = pool.map(run_match, range(MATCHES))
            win_counts.update(results)
    else:
        for i in range(MATCHES):
            win_counts.update((run_match(i),))

    player_name, wins = win_counts.most_common(1)[0]
    print(f"{player_name} won the most with {wins}/{MATCHES} ({(wins / MATCHES) * 100:.2f}%)")

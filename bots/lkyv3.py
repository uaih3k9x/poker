# Your code here
from phevaluator.evaluator import evaluate_cards
from logic import Move, Game, Player, HandRank, RockyPlayer, RandomPlayer
from collections import Counter
from multiprocessing import Pool, cpu_count
import random

ALL_CARDS = tuple(rank + suit for suit in 'dhsc' for rank in '23456789TJQKA')
RANK_VALUES = {rank: value for value, rank in enumerate('23456789TJQKA', start=2)}
AGGRESSIVE_MOVES = {Move.BET, Move.RAISE, Move.ALL_IN}
BIG_BLIND = 100

MATCHES = 1000
PARALLEL = False

class MyPlayer(Player):
    name = 'Default Name 3 v3'
    image_path = 'images/your_image.png'

    def __init__(self):
        super().__init__()
        self._is_button = False

    # ── Utility ──────────────────────────────────────────────

    @staticmethod
    def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def preflop_strength_for(cards: list[str]) -> float:
        """Continuous preflop hand strength (0.23 - 0.96)."""
        r1, r2 = (RANK_VALUES[c[0]] for c in cards)
        suited = cards[0][1] == cards[1][1]
        high, low = max(r1, r2), min(r1, r2)
        if r1 == r2:
            return min(0.96, max(0.48, 0.4 + high * 0.04))
        score = 0.16 + high * 0.027 + low * 0.014
        if suited:
            score += 0.05
        gap = high - low
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
        return max(0.23, min(0.88, score))

    def preflop_strength(self) -> float:
        return self.preflop_strength_for(self.cards)

    # ── State extraction from round_history ──────────────────

    def commitments(self, round_history: list[tuple[Move, int]]) -> tuple[int, int]:
        """Extract (my_commitment, opp_commitment) from round_history."""
        my_commitment, opp_commitment = 0, 0
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
        """Estimate total pot = previous streets baseline + current street commitments."""
        baseline = 0 if not community_cards else 200 + max(0, len(community_cards) - 3) * 50
        return baseline + my_commitment + opp_commitment

    def opponent_aggression_count(self, round_history: list[tuple[Move, int]]) -> int:
        """Count only opponent's aggressive actions this street."""
        count = 0
        actor_is_me = len(round_history) % 2 == 0
        for move, _ in round_history:
            if (not actor_is_me) and move in AGGRESSIVE_MOVES:
                count += 1
            actor_is_me = not actor_is_me
        return count

    def opponent_allin(self, round_history: list[tuple[Move, int]]) -> bool:
        """Check if opponent (not us) went all-in this street."""
        actor_is_me = len(round_history) % 2 == 0
        for move, _ in round_history:
            if (not actor_is_me) and move == Move.ALL_IN:
                return True
            actor_is_me = not actor_is_me
        return False

    def opponent_profile(self) -> tuple[bool, bool]:
        """Returns (is_tight, is_loose) based on showdown history."""
        if len(self.hands_shown) < 2:
            return False, False
        avg = sum(
            self.preflop_strength_for(cards) for cards, _ in self.hands_shown
        ) / len(self.hands_shown)
        return avg >= 0.68, avg <= 0.48

    # ── Action helper ────────────────────────────────────────

    def aggressive_action(self, valid_moves, min_bet, max_bet, target, fallback):
        """Choose best aggressive action, falling back gracefully."""
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

    # ── Monte Carlo equity ───────────────────────────────────

    def monte_carlo_equity(self, community_cards: list[str]) -> float:
        """Estimate win probability via sampling."""
        known = set(self.cards + community_cards)
        remaining = [c for c in ALL_CARDS if c not in known]
        cards_to_draw = 2 + (5 - len(community_cards))
        samples = {3: 100, 4: 75, 5: 50}[len(community_cards)]
        wins = 0.0
        for _ in range(samples):
            draw = random.sample(remaining, cards_to_draw)
            opp_cards = draw[:2]
            full_board = community_cards + draw[2:]
            my_rank = evaluate_cards(*full_board, *self.cards)
            opp_rank = evaluate_cards(*full_board, *opp_cards)
            if my_rank < opp_rank:
                wins += 1
            elif my_rank == opp_rank:
                wins += 0.5
        return wins / samples

    # ── Draw & pair detection ────────────────────────────────

    def draw_flags(self, community_cards: list[str]) -> tuple[bool, bool]:
        """Returns (strong_draw, gutshot). strong_draw = flush_draw or open-ended."""
        all_cards = community_cards + self.cards
        suit_counts = Counter(c[1] for c in all_cards)
        flush_draw = len(community_cards) < 5 and max(suit_counts.values()) >= 4
        rank_set = set(RANK_VALUES[c[0]] for c in all_cards)
        if 14 in rank_set:
            rank_set.add(1)
        open_ended, gutshot = False, False
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
        return flush_draw or open_ended, gutshot

    def pair_flags(self, community_cards: list[str]) -> tuple[bool, bool]:
        """Returns (overpair, top_pair)."""
        if not community_cards:
            return False, False
        board_ranks = [RANK_VALUES[c[0]] for c in community_cards]
        hole_ranks = [RANK_VALUES[c[0]] for c in self.cards]
        top_board = max(board_ranks)
        overpair = hole_ranks[0] == hole_ranks[1] and hole_ranks[0] > top_board
        top_pair = any(r == top_board for r in hole_ranks)
        return overpair, top_pair

    # ── Preflop ──────────────────────────────────────────────

    def preflop_move(self, valid_moves, min_bet, max_bet, to_call, pot_odds, round_history):
        strength = self.preflop_strength()
        opp_tight, _ = self.opponent_profile()
        facing_allin = self.opponent_allin(round_history)

        # Facing all-in: only premium
        if facing_allin:
            if strength >= 0.82 and Move.CALL in valid_moves:
                return Move.CALL
            return Move.FOLD

        # No raise to face
        if to_call == 0:
            if self._is_button:
                # Button open: wider range, variable sizing
                if strength >= 0.82:
                    return self.aggressive_action(valid_moves, min_bet, max_bet, 325, Move.CHECK)
                if strength >= 0.36:
                    return self.aggressive_action(valid_moves, min_bet, max_bet, 250, Move.CHECK)
                if strength >= 0.26:
                    return self.aggressive_action(valid_moves, min_bet, max_bet, 200, Move.CHECK)
                return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD
            else:
                # BB check option (opponent limped)
                if strength >= 0.82:
                    return self.aggressive_action(valid_moves, min_bet, max_bet, 350, Move.CHECK)
                if strength >= 0.50:
                    return self.aggressive_action(valid_moves, min_bet, max_bet, 250, Move.CHECK)
                return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        # Facing a raise
        stack_pressure = to_call / max(max_bet, 1)

        # Premium: 3bet / jam
        if strength >= 0.9 or (strength >= 0.84 and stack_pressure > 0.22):
            if Move.ALL_IN in valid_moves and max_bet <= 2200:
                return Move.ALL_IN
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.4))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        # Strong: 3bet
        bet_size_bb = max(2.0, to_call / BIG_BLIND)
        if strength >= 0.74 and bet_size_bb <= 5 and Move.RAISE in valid_moves:
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.2))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        # Defend: tight threshold + call cap
        defend_threshold = max(0.46, pot_odds + 0.14)
        if opp_tight and to_call <= BIG_BLIND:
            defend_threshold -= 0.04

        max_call = max(500, int(max_bet * 0.14))
        if strength >= defend_threshold and to_call <= max_call and Move.CALL in valid_moves:
            return Move.CALL

        return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

    # ── Postflop ─────────────────────────────────────────────

    def postflop_move(self, community_cards, valid_moves, round_history,
                      min_bet, max_bet, to_call, pot, pot_odds):
        equity = self.monte_carlo_equity(community_cards)
        opp_tight, opp_loose = self.opponent_profile()
        strong_draw, gutshot = self.draw_flags(community_cards)
        overpair, top_pair = self.pair_flags(community_cards)

        opp_aggr = self.opponent_aggression_count(round_history)
        facing_heat = to_call > 0 and opp_aggr >= 2
        facing_allin = self.opponent_allin(round_history)

        # Draw bonus to reduce MC noise
        if strong_draw and gutshot:
            equity = min(1.0, equity + 0.02)

        # === Facing a bet / raise ===
        if to_call > 0:
            # Hard fold: tight opponent heat on turn/river
            if facing_heat and opp_tight and len(community_cards) >= 4 and equity < 0.68:
                return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

            # Hard fold: opponent all-in
            if facing_allin:
                if len(community_cards) == 5 and equity < 0.74:
                    return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD
                if len(community_cards) < 5 and equity < 0.70 and not strong_draw:
                    return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

            # Very strong: raise for value
            if equity >= 0.78:
                _, opp_c = self.commitments(round_history)
                target = max(min_bet, int(opp_c + max(to_call * 1.6, pot * 0.5)))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            # Strong: raise (not under heat)
            if equity >= 0.62 and not facing_heat:
                _, opp_c = self.commitments(round_history)
                target = max(min_bet, int(opp_c + max(to_call * 1.25, pot * 0.3)))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            # Pot-odds calling with adjustments
            call_threshold = pot_odds + 0.06
            if opp_loose:
                call_threshold -= 0.03
            if opp_tight:
                call_threshold += 0.02
            if facing_heat:
                call_threshold += 0.05
            if strong_draw and len(community_cards) < 5:
                call_threshold -= 0.05

            max_call_amount = max(900, int(max_bet * 0.25))
            if equity >= call_threshold and to_call <= max_call_amount and Move.CALL in valid_moves:
                return Move.CALL

            # Cheap draw call
            if strong_draw and to_call <= 250 and Move.CALL in valid_moves:
                return Move.CALL

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        # === Acting first (to_call == 0) ===
        if self._is_button:
            # IP: wider c-bet, smaller sizing
            if equity >= 0.66:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.70)), Move.CHECK)
            if equity >= 0.50:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.50)), Move.CHECK)
            if len(community_cards) == 3 and equity >= 0.38:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.35)), Move.CHECK)
            if strong_draw and len(community_cards) < 5 and not opp_loose:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.40)), Move.CHECK)
        else:
            # OOP: tighter thresholds
            if equity >= 0.70:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.75)), Move.CHECK)
            if equity >= 0.58:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.55)), Move.CHECK)
            if strong_draw and len(community_cards) < 5 and not opp_loose:
                return self.aggressive_action(valid_moves, min_bet, max_bet,
                                              max(min_bet, int(pot * 0.45)), Move.CHECK)

        return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

    # ── Main entry ───────────────────────────────────────────

    def move(self, community_cards, valid_moves, round_history, min_bet, max_bet):
        my_commitment, opp_commitment = self.commitments(round_history)
        to_call = max(0, opp_commitment - my_commitment)
        pot = self.estimate_pot(community_cards, my_commitment, opp_commitment)
        pot_odds = to_call / (pot + to_call) if to_call > 0 else 0.0

        if not community_cards:
            # Detect position only on first preflop action
            if len(round_history) <= 3:
                self._is_button = self.pot_commitment <= 50
            return self.preflop_move(valid_moves, min_bet, max_bet, to_call, pot_odds, round_history)

        return self.postflop_move(community_cards, valid_moves, round_history,
                                  min_bet, max_bet, to_call, pot, pot_odds)

def run_match(_: int) -> str:
    """Run a single match and return the winner's name."""
    p1, p2 = MyPlayer(), RockyPlayer()
    game = Game(p1, p2, debug=False)
    return game.simulate_hands().name

if __name__ == '__main__':
    win_counts = Counter()
    if (PARALLEL):
        with Pool(cpu_count()) as pool:
            results = pool.map(run_match, range(MATCHES))
            win_counts.update(results)
    else:
        for i in range(MATCHES):
            win_counts.update((run_match(i),))

    player_name, wins = win_counts.most_common(1)[0]
    print(f'{player_name} won the most with {wins}/{MATCHES} ({(wins / MATCHES) * 100:.2f}%)')

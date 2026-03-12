# Your code here
from phevaluator.evaluator import evaluate_cards
from logic import Move, Game, Player, HandRank, RockyPlayer, RandomPlayer
from collections import Counter
from multiprocessing import Pool, cpu_count
import random
import math

ALL_CARDS = tuple(rank + suit for suit in 'dhsc' for rank in '23456789TJQKA')
RANK_VALUES = {rank: value for value, rank in enumerate('23456789TJQKA', start=2)}

# Feel free to set a seed for testing, otherwise leave commmented out to test your bot in a variety of random spots
# Note that you cannot set a seed and run the simulation in parallel
# random.seed(6767)

# How many heads up matches you want to simulate
MATCHES = 1000
# For development I recommend not processing in parallel as it can make it much harder to find errors
PARALLEL = False

class MyPlayer(Player):
    name = 'Default Name 3 v2'
    image_path = 'images/your_image.png' # Optional

    def __init__(self):
        super().__init__()
        self._hand_start_chips = self.chips

    @staticmethod
    def chen_score(cards: list[str]) -> int:
        """Chen formula for any two cards. Returns integer score (higher = stronger)."""
        rank1, suit1 = cards[0][0], cards[0][1]
        rank2, suit2 = cards[1][0], cards[1][1]
        values = {'A': 10, 'K': 8, 'Q': 7, 'J': 6, 'T': 5,
                  '9': 4.5, '8': 4, '7': 3.5, '6': 3, '5': 2.5,
                  '4': 2, '3': 1.5, '2': 1}
        order = '23456789TJQKA'

        score = max(values[rank1], values[rank2])
        if rank1 == rank2:
            return math.ceil(max(score * 2, 5))

        if suit1 == suit2:
            score += 2

        idx1, idx2 = order.index(rank1), order.index(rank2)
        gap = abs(idx1 - idx2) - 1
        if gap == 1: score -= 1
        elif gap == 2: score -= 2
        elif gap == 3: score -= 4
        elif gap >= 4: score -= 5

        if gap <= 1 and max(idx1, idx2) < order.index('Q'):
            score += 1
        return math.ceil(score)

    def chen_formula(self) -> int:
        return self.chen_score(self.cards)

    def get_hand_type(self, community_cards: list[str]) -> HandRank:
        # Handle pre flop calls
        if not community_cards:
            return HandRank.ONE_PAIR if self.cards[0][0] == self.cards[1][0] else HandRank.HIGH_CARD

        rank = evaluate_cards(*community_cards, *self.cards)
        for hand_type in HandRank:
            if rank <= hand_type.value:
                return hand_type
        raise IndexError(f'Hand Rank Out Of Range: {rank}')

    def monte_carlo_equity(self, community_cards: list[str]) -> float:
        """Estimate win probability against a random opponent hand via sampling."""
        known = set(self.cards + community_cards)
        remaining = [c for c in ALL_CARDS if c not in known]
        cards_to_draw = 2 + (5 - len(community_cards))  # 2 opp + remaining board
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

    def has_draws(self, community_cards: list[str]) -> tuple[bool, bool]:
        """Returns (flush_draw, straight_draw). Only meaningful before river."""
        all_cards = community_cards + self.cards
        # Flush draw: 4 cards of same suit
        suit_counts = Counter(c[1] for c in all_cards)
        flush_draw = max(suit_counts.values()) >= 4

        # Straight draw: 4 out of 5 consecutive ranks
        rank_set = set(RANK_VALUES[c[0]] for c in all_cards)
        if 14 in rank_set:
            rank_set.add(1)
        straight_draw = False
        for start in range(1, 11):
            if len(set(range(start, start + 5)) & rank_set) == 4:
                straight_draw = True
                break

        return flush_draw, straight_draw

    def opponent_profile(self) -> tuple[bool, bool]:
        """Analyze opponent showdown history. Returns (is_tight, is_loose)."""
        if len(self.hands_shown) < 3:
            return False, False
        avg_chen = sum(
            self.chen_score(cards) for cards, _ in self.hands_shown
        ) / len(self.hands_shown)
        return avg_chen >= 12, avg_chen <= 6

    def preflop_action(self, valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        chen = self.chen_formula()
        is_button = self.pot_commitment <= 50
        facing_raise = any(h[0] == Move.RAISE for h in round_history)
        facing_allin = any(h[0] == Move.ALL_IN for h in round_history)
        opp_tight, _ = self.opponent_profile()

        # Facing all-in — only call with premium hands
        if facing_allin:
            if chen >= 12 and Move.CALL in valid_moves:
                return Move.CALL
            if chen >= 12 and Move.ALL_IN in valid_moves:
                return Move.ALL_IN
            return Move.FOLD

        # BB option (opponent limped/called) — raise or check
        if Move.CHECK in valid_moves:
            if chen >= 10 and Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, 350)))
            if chen >= 6 and Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, 250)))
            return Move.CHECK

        # Facing a raise — re-raise, call (with cap), or fold
        if facing_raise:
            if chen >= 14:
                return Move.ALL_IN
            if chen >= 10:
                if Move.RAISE in valid_moves:
                    return (Move.RAISE, min(max_bet, max(min_bet, round_history[-1][1] * 3)))
                return Move.CALL
            if chen >= 7 and Move.CALL in valid_moves:
                to_call = round_history[-1][1] - self.pot_commitment
                if to_call <= max(700, int(max_bet * 0.18)):
                    return Move.CALL
            return Move.FOLD

        # Standard open — position-aware, NO limp
        open_threshold = 5 if is_button else 6
        if opp_tight:
            open_threshold -= 1

        if chen >= 10 and Move.RAISE in valid_moves:
            return (Move.RAISE, min(max_bet, max(min_bet, 300)))
        if chen >= open_threshold and Move.RAISE in valid_moves:
            return (Move.RAISE, min(max_bet, max(min_bet, 250)))
        return Move.FOLD

    def estimate_pot(self, round_history: list[tuple[Move, int]]) -> int:
        """Estimate current pot from tracked hand investment + current street actions."""
        # Chips we invested in previous streets (not counting this street's pot_commitment)
        our_prev_investment = self._hand_start_chips - self.chips - self.pot_commitment
        # Assume opponent roughly matched us on previous streets
        prev_pot = max(our_prev_investment * 2, 200)
        # Add current street's commitments (players alternate in history)
        commitments = [0, 0]
        for i, (_, amount) in enumerate(round_history):
            commitments[i % 2] = max(commitments[i % 2], amount)
        return prev_pot + commitments[0] + commitments[1]

    def postflop_action(
            self,
            community_cards: list[str],
            valid_moves: list[Move],
            round_history: list[tuple[Move, int]],
            min_bet: int,
            max_bet: int
    ) -> tuple[Move, int] | Move:
        equity = self.monte_carlo_equity(community_cards)
        pot = self.estimate_pot(round_history)
        facing_bet = Move.CALL in valid_moves
        facing_allin = any(h[0] == Move.ALL_IN for h in round_history)
        flush_draw, straight_draw = self.has_draws(community_cards)
        has_draw = (flush_draw or straight_draw) and len(community_cards) < 5
        opp_tight, opp_loose = self.opponent_profile()

        # Facing heat: multiple aggressive actions this street
        aggression_count = sum(1 for m, _ in round_history if m in (Move.BET, Move.RAISE, Move.ALL_IN))
        facing_heat = facing_bet and aggression_count >= 2

        call_cost = 0
        pot_odds = 0.0
        if facing_bet and round_history:
            call_cost = round_history[-1][1] - self.pot_commitment
            pot_odds = call_cost / (pot + call_cost) if (pot + call_cost) > 0 else 0

        # === Facing a bet / raise ===
        if facing_bet:
            # Very strong: raise for max value
            if equity >= 0.78:
                if Move.RAISE in valid_moves:
                    return (Move.RAISE, min(max_bet, max(min_bet, int(pot * 0.75))))
                return Move.CALL

            # Strong: raise (only if not under heat) or call
            if equity >= 0.62:
                if not facing_heat and not facing_allin and Move.RAISE in valid_moves:
                    return (Move.RAISE, min(max_bet, max(min_bet, int(pot * 0.5))))
                return Move.CALL

            # Pot-odds-based calling with call cap
            call_threshold = pot_odds + 0.06
            if opp_loose: call_threshold -= 0.03
            if opp_tight: call_threshold += 0.02
            if has_draw: call_threshold -= 0.05
            if facing_allin: call_threshold += 0.08
            if facing_heat: call_threshold += 0.05

            max_call = max(900, int(max_bet * 0.25))
            if equity >= call_threshold and call_cost <= max_call and Move.CALL in valid_moves:
                return Move.CALL

            # Cheap draw call
            if has_draw and call_cost <= 250 and Move.CALL in valid_moves:
                return Move.CALL

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        # === Acting first (no bet to face) ===
        if equity >= 0.70 and Move.BET in valid_moves:
            return (Move.BET, min(max_bet, max(min_bet, int(pot * 0.7))))

        if equity >= 0.55 and Move.BET in valid_moves:
            return (Move.BET, min(max_bet, max(min_bet, int(pot * 0.5))))

        # Flop-only probe bet (not turn/river to avoid over-betting weak hands)
        if equity >= 0.45 and len(community_cards) == 3 and Move.BET in valid_moves:
            return (Move.BET, min(max_bet, max(min_bet, int(pot * 0.35))))

        # Semi-bluff with draws (not vs loose, not on river)
        if has_draw and not opp_loose and Move.BET in valid_moves:
            return (Move.BET, min(max_bet, max(min_bet, int(pot * 0.4))))

        return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

    def move(self, community_cards: list[str], valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        if not community_cards:
            # Record chips at hand start (current chips + blind already posted)
            self._hand_start_chips = self.chips + self.pot_commitment
            return self.preflop_action(valid_moves, round_history, min_bet, max_bet)
        return self.postflop_action(community_cards, valid_moves, round_history, min_bet, max_bet)

def run_match(_: int) -> str:
    """Run a single match and return the winner's name."""
    p1, p2 = MyPlayer(), RandomPlayer()
    game = Game(p1, p2, debug=False)
    return game.simulate_hands().name

if __name__ == '__main__':
    win_counts = Counter()
    # This runs the large number of matches in parallel, which drastically speeds up computation time
    if (PARALLEL):
        with Pool(cpu_count()) as pool:
            results = pool.map(run_match, range(MATCHES))
            win_counts.update(results)
    else:
        for i in range(MATCHES):
            win_counts.update((run_match(i),)) 

    player_name, wins = win_counts.most_common(1)[0]
    print(f'{player_name} won the most with {wins}/{MATCHES} ({(wins / MATCHES) * 100:.2f}%)')

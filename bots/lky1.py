# Your code here
from phevaluator.evaluator import evaluate_cards
from logic import Move, Game, Player, HandRank, RockyPlayer, RandomPlayer
from collections import Counter
from multiprocessing import Pool, cpu_count
import random
import math

# Feel free to set a seed for testing, otherwise leave commmented out to test your bot in a variety of random spots
# Note that you cannot set a seed and run the simulation in parallel
# random.seed(6767)

# How many heads up matches you want to simulate
MATCHES = 1000
# For development I recommend not processing in parallel as it can make it much harder to find errors
PARALLEL = False

class MyPlayer(Player):
    name = 'Default Name 3 v1'
    image_path = 'images/your_image.png' # Optional

    CHEN_RANK_VALUES = {
        'A': 10, 'K': 8, 'Q': 7, 'J': 6, 'T': 5,
        '9': 4.5, '8': 4, '7': 3.5, '6': 3, '5': 2.5,
        '4': 2, '3': 1.5, '2': 1,
    }
    RANK_ORDER = '23456789TJQKA'

    def __init__(self):
        super().__init__()
        self._hand_start_chips = self.chips

    def chen_formula(self) -> int:
        """Calculate Chen formula score for pre-flop hand strength.
        Returns an integer score (higher = stronger)."""
        rank1, suit1 = self.cards[0][0], self.cards[0][1]
        rank2, suit2 = self.cards[1][0], self.cards[1][1]

        val1 = self.CHEN_RANK_VALUES[rank1]
        val2 = self.CHEN_RANK_VALUES[rank2]

        # Step 1: Start with highest card value
        score = max(val1, val2)

        # Step 2: Pair — double the score, minimum 5
        if rank1 == rank2:
            score = max(score * 2, 5)
            return math.ceil(score)

        # Step 3: Suited — add 2
        if suit1 == suit2:
            score += 2

        # Step 4: Gap penalty
        idx1 = self.RANK_ORDER.index(rank1)
        idx2 = self.RANK_ORDER.index(rank2)
        gap = abs(idx1 - idx2) - 1

        if gap == 1:
            score -= 1
        elif gap == 2:
            score -= 2
        elif gap == 3:
            score -= 4
        elif gap >= 4:
            score -= 5

        # Step 5: Round-up bonus for connected/1-gap hands where highest card < Q
        if gap <= 1 and max(idx1, idx2) < self.RANK_ORDER.index('Q'):
            score += 1

        return math.ceil(score)

    def get_hand_type(self, community_cards: list[str]) -> HandRank:
        # Handle pre flop calls
        if not community_cards:
            return HandRank.ONE_PAIR if self.cards[0][0] == self.cards[1][0] else HandRank.HIGH_CARD

        rank = evaluate_cards(*community_cards, *self.cards)
        for hand_type in HandRank:
            if rank <= hand_type.value:
                return hand_type
        raise IndexError(f'Hand Rank Out Of Range: {rank}')

    def get_equity(self, community_cards: list[str], samples: int = 5000) -> float:
        """Placeholder equity calculation function. You do not have to implement a function like this but some sort of equity calculation is highly recommended."""

        return 0.0

    def get_strength(self, community_cards: list[str]) -> float:
        """Calculate hand strength as a percentage (0.0 to 1.0) based on current hand and community cards."""
        rank = evaluate_cards(*community_cards, *self.cards)

        return (7462 - rank) / 7462  # Normalize to [0, 1], where 1 is the best hand (royal flush)

    def preflop_action(self, valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        chen = self.chen_formula()
        facing_raise = any(h[0] == Move.RAISE for h in round_history)
        facing_allin = any(h[0] == Move.ALL_IN for h in round_history)

        # Facing all-in — only call with premium hands
        if facing_allin:
            if chen >= 12 and Move.CALL in valid_moves:
                return Move.CALL
            if chen >= 12 and Move.ALL_IN in valid_moves:
                return Move.ALL_IN
            return Move.FOLD

        # BB option (opponent limped) — raise for value or check
        if Move.CHECK in valid_moves:
            if chen >= 10 and Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, 300)))
            if chen >= 6 and Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, 250)))
            return Move.CHECK

        # Facing a raise — re-raise, call, or fold
        if facing_raise:
            if chen >= 14:
                return Move.ALL_IN
            if chen >= 10:
                if Move.RAISE in valid_moves:
                    return (Move.RAISE, min(max_bet, max(min_bet, round_history[-1][1] * 3)))
                return Move.CALL
            if chen >= 7 and Move.CALL in valid_moves:
                return Move.CALL
            return Move.FOLD

        # Standard open — raise with good hands, limp marginal, fold trash
        if chen >= 10 and Move.RAISE in valid_moves:
            return (Move.RAISE, min(max_bet, max(min_bet, 300)))
        if chen >= 6 and Move.RAISE in valid_moves:
            return (Move.RAISE, min(max_bet, max(min_bet, 250)))
        if chen >= 3 and Move.CALL in valid_moves:
            return Move.CALL
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
        strength = self.get_strength(community_cards)
        pot = self.estimate_pot(round_history)
        facing_bet = Move.CALL in valid_moves
        facing_allin = any(h[0] == Move.ALL_IN for h in round_history)

        # Calculate call cost when facing a bet
        call_cost = 0
        if facing_bet and round_history:
            call_cost = round_history[-1][1] - self.pot_commitment

        # Monster: Flush or better (strength > 0.78)
        if strength > 0.78:
            if Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, pot)))
            if Move.BET in valid_moves:
                return (Move.BET, min(max_bet, max(min_bet, pot * 2 // 3)))
            if Move.CALL in valid_moves:
                return Move.CALL
            return Move.CHECK

        # Strong: Three of a Kind or better (strength > 0.67)
        if strength > 0.67:
            if Move.BET in valid_moves:
                return (Move.BET, min(max_bet, max(min_bet, pot * 2 // 3)))
            if Move.RAISE in valid_moves:
                return (Move.RAISE, min(max_bet, max(min_bet, round_history[-1][1] * 2)))
            if facing_bet:
                return Move.CALL
            return Move.CHECK

        # Good: Two Pair or better (strength > 0.55)
        if strength > 0.55:
            if Move.BET in valid_moves:
                return (Move.BET, min(max_bet, max(min_bet, pot // 2)))
            if facing_bet:
                return Move.CALL
            return Move.CHECK

        # Any Pair (strength > 0.17)
        if strength > 0.17:
            if Move.BET in valid_moves:
                return (Move.BET, min(max_bet, max(min_bet, pot // 3)))
            if facing_allin:
                # Only call all-in with strong pair (top pair range)
                if strength > 0.40:
                    return Move.CALL
                return Move.FOLD
            if facing_bet:
                return Move.CALL
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        # High card — small bluff bet (Random folds ~25%), call tiny bets
        if Move.BET in valid_moves:
            return (Move.BET, min(max_bet, max(min_bet, pot // 4)))
        if Move.CHECK in valid_moves:
            return Move.CHECK
        if facing_bet and call_cost <= pot // 4:
            return Move.CALL
        return Move.FOLD

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
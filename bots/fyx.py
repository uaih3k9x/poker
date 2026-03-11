# Your code here
from phevaluator.evaluator import evaluate_cards
from logic import Move, Game, Player, HandRank, RockyPlayer, RandomPlayer
from collections import Counter
from multiprocessing import Pool, cpu_count
import random

# Feel free to set a seed for testing, otherwise leave commmented out to test your bot in a variety of random spots
# Note that you cannot set a seed and run the simulation in parallel
# random.seed(6767)

# How many heads up matches you want to simulate
MATCHES = 1000
# For development I recommend not processing in parallel as it can make it much harder to find errors
PARALLEL = False


class MyPlayer(Player):
    name = 'FYX'
    image_path = 'images/your_image.png'

    # Rank order for gap/index calculations
    _RANKS = '23456789TJQKA'

    # Chen formula card values
    _CHEN = {
        '2': 2, '3': 2, '4': 2, '5': 2,
        '6': 2.5, '7': 3, '8': 3.5, '9': 4,
        'T': 5, 'J': 6, 'Q': 7, 'K': 8, 'A': 10
    }

    # Full deck for Monte Carlo sampling
    _ALL_CARDS = [r + s for s in 'dhsc' for r in '23456789TJQKA']

    def __init__(self):
        super().__init__()
        # True = we are the button (in position postflop), False = we are the BB
        self._in_pos = True

    # ─── Position Detection ───────────────────────────────────────────────────

    def _update_position(self):
        """
        Detect position from pot_commitment.
        Called every move; only updates when pot_commitment matches blind sizes
        (i.e., at the very first action of a new hand before any raise).
        """
        if self.pot_commitment == 50:
            self._in_pos = True   # We posted the small blind = we are the Button
        elif self.pot_commitment == 100:
            self._in_pos = False  # We posted the big blind = we are the BB

    # ─── Preflop Hand Strength ────────────────────────────────────────────────

    def _preflop_score(self) -> float:
        """
        Simplified Chen formula normalized to [0, 1].
        Higher = stronger starting hand.
        """
        r1, s1 = self.cards[0][0], self.cards[0][1]
        r2, s2 = self.cards[1][0], self.cards[1][1]

        score = max(self._CHEN[r1], self._CHEN[r2])

        if r1 == r2:
            # Pair: double the higher card value, minimum 5
            score = max(score * 2, 5)
        else:
            if s1 == s2:
                score += 2  # Suited bonus
            gap = abs(self._RANKS.index(r1) - self._RANKS.index(r2)) - 1
            # Gap penalty: connected=+1, 1gap=0, 2gap=-1, 3gap=-2, 4+gap=-4
            score += [1, 0, -1, -2, -4][min(gap, 4)]

        # Normalize: worst useful hand ~0, AA ~20 → scale by 20
        return min(1.0, max(0.0, score / 20.0))

    # ─── Equity Estimation (Monte Carlo) ─────────────────────────────────────

    def _equity(self, community_cards: list[str], samples: int = 45) -> float:
        """
        Estimate win probability by randomly sampling possible opponent hands
        and completing the board. Lower phevaluator rank = better hand.
        Kept at 45 samples to stay well under the 1ms per-move constraint.
        """
        known = set(self.cards + community_cards)
        deck = [c for c in self._ALL_CARDS if c not in known]
        need = 5 - len(community_cards)  # board cards still to come

        wins = 0.0
        for _ in range(samples):
            draw = random.sample(deck, 2 + need)
            opp_cards = draw[:2]
            board = community_cards + draw[2:]
            my_rank = evaluate_cards(*board, *self.cards)
            op_rank = evaluate_cards(*board, *opp_cards)
            if my_rank < op_rank:
                wins += 1.0
            elif my_rank == op_rank:
                wins += 0.5
        return wins / samples

    # ─── Opponent Modeling ────────────────────────────────────────────────────

    def _opp_type(self) -> str:
        """
        Classify opponent from showdown history (last 20 hands to stay fast):
          'tight'  – only shows strong hands (Rocky-like)
          'loose'  – shows weak/random hands (Rando-like)
          'normal' – unknown or balanced
        """
        shown = self.hands_shown[-20:]  # cap at last 20 for speed
        if len(shown) < 6:
            return 'normal'
        strong = sum(1 for _, rank in shown if rank <= HandRank.STRAIGHT)
        ratio = strong / len(shown)
        if ratio > 0.60:
            return 'tight'
        if ratio < 0.25:
            return 'loose'
        return 'normal'

    # ─── Bet Sizing ───────────────────────────────────────────────────────────

    def _bet(self, base: int, fraction: float, min_bet: int, max_bet: int) -> int:
        """Return a legal bet amount = fraction * base, clamped to [min_bet, max_bet]."""
        size = int(base * fraction)
        return min(max_bet, max(min_bet, size))

    def _pot_from_history(self, round_history: list) -> int:
        """
        Estimate the current street's pot contribution from round_history.
        The pot = (aggressor's last total commit) + (our current commit).
        Falls back to 200 chips (2 BB) when there's been no action.
        """
        aggr = {Move.BET, Move.RAISE, Move.ALL_IN, Move.CALL}
        last_amount = next(
            (a for m, a in reversed(round_history) if m in aggr), 0
        )
        street_pot = last_amount + self.pot_commitment
        return max(street_pot, 200)

    # ─── Main Decision Logic ──────────────────────────────────────────────────

    def move(
        self,
        community_cards: list[str],
        valid_moves: list[Move],
        round_history: list[tuple[Move, int]],
        min_bet: int,
        max_bet: int
    ) -> tuple[Move, int] | Move:

        preflop = not community_cards
        self._update_position()

        # ── Short Stack: Push / Fold mode (< 15 BB total stack) ──────────────
        total_stack = self.chips + self.pot_commitment
        if total_stack <= 1500:
            if preflop:
                strength = self._preflop_score()
                threshold = 0.44 if self._in_pos else 0.50
                go = strength >= threshold
            else:
                go = self._equity(community_cards, samples=45) >= 0.50

            if go:
                if Move.ALL_IN in valid_moves:
                    return Move.ALL_IN
                if Move.RAISE in valid_moves:
                    return Move.RAISE, max_bet
                if Move.CALL in valid_moves:
                    return Move.CALL
                if Move.CHECK in valid_moves:
                    return Move.CHECK
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        opp = self._opp_type()

        # ── PRE-FLOP ─────────────────────────────────────────────────────────
        if preflop:
            score = self._preflop_score()
            in_pos = self._in_pos

            # Adjust aggression thresholds based on opponent type
            if opp == 'tight':
                # Steal liberally; fold to their 3-bets unless strong
                open_t, defend_t = 0.30, 0.36
            elif opp == 'loose':
                # Play tighter for value; defend a bit wider
                open_t, defend_t = 0.50, 0.48
            else:
                open_t, defend_t = 0.40, 0.44

            # Are we facing a bet/raise?
            facing_raise = (Move.CALL in valid_moves and Move.CHECK not in valid_moves)

            if facing_raise:
                if score >= 0.78:
                    # Premium hand: 3-bet (re-raise ~3x)
                    if Move.RAISE in valid_moves:
                        last_bet = round_history[-1][1] if round_history else min_bet
                        amt = self._bet(last_bet, 3.0, min_bet, max_bet)
                        return Move.RAISE, amt
                    return Move.ALL_IN
                if score >= defend_t:
                    return Move.CALL
                return Move.FOLD

            # We're opening or have already called (BB check option)
            if score >= 0.78:
                # Premium: raise to 4 BB
                target = self._bet(400, 1.0, min_bet, max_bet)
                if Move.RAISE in valid_moves:
                    return Move.RAISE, target
                if Move.BET in valid_moves:
                    return Move.BET, target
                if Move.ALL_IN in valid_moves:
                    return Move.ALL_IN

            elif score >= open_t:
                # Playable: raise to 2.5 BB
                target = self._bet(250, 1.0, min_bet, max_bet)
                if in_pos:
                    if Move.RAISE in valid_moves:
                        return Move.RAISE, target
                    if Move.BET in valid_moves:
                        return Move.BET, target
                else:
                    # OOP: only raise with stronger hands, else check
                    if score >= defend_t and Move.RAISE in valid_moves:
                        return Move.RAISE, target

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        # ── POST-FLOP ─────────────────────────────────────────────────────────
        equity = self._equity(community_cards)
        pot = self._pot_from_history(round_history)

        # Identify if we face an active bet/raise
        aggr_moves = {Move.BET, Move.RAISE, Move.ALL_IN}
        last_aggr = next(
            ((m, a) for m, a in reversed(round_history) if m in aggr_moves), None
        )

        if last_aggr:
            # ── Facing a bet ─────────────────────────────────────────────────
            call_amt = last_aggr[1] - self.pot_commitment
            pot_after_call = pot + call_amt
            pot_odds = call_amt / pot_after_call if pot_after_call > 0 else 0.33

            if equity >= 0.65:
                # Strong hand: raise for value
                if Move.RAISE in valid_moves:
                    return Move.RAISE, self._bet(pot, 0.75, min_bet, max_bet)
                if Move.ALL_IN in valid_moves and equity >= 0.82:
                    return Move.ALL_IN
                return Move.CALL

            if equity >= max(pot_odds + 0.05, 0.40):
                # Decent hand or good odds: call
                return Move.CALL

            if equity >= 0.30 and call_amt <= self.chips * 0.10:
                # Cheap draw: call
                return Move.CALL

            return Move.FOLD

        else:
            # ── No bet to face: we can check or bet ──────────────────────────
            if equity >= 0.58:
                # Value bet: 65% of estimated pot
                bet = self._bet(pot, 0.65, min_bet, max_bet)
                if Move.BET in valid_moves:
                    return Move.BET, bet
                if Move.RAISE in valid_moves:
                    return Move.RAISE, bet

            elif equity >= 0.48 and self._in_pos:
                # Positional c-bet / probe: 45% pot
                bet = self._bet(pot, 0.45, min_bet, max_bet)
                if Move.BET in valid_moves:
                    return Move.BET, bet

            elif equity < 0.38 and self._in_pos and random.random() < 0.25:
                # Occasional bluff in position: 50% pot
                bet = self._bet(pot, 0.50, min_bet, max_bet)
                if Move.BET in valid_moves:
                    return Move.BET, bet

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD


def run_match(_: int) -> str:
    """Run a single match and return the winner's name."""
    p1, p2 = MyPlayer(), RockyPlayer()
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

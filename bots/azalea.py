# Your code here
from phevaluator.evaluator import evaluate_cards
from logic import Move, Game, Player, RandomPlayer
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

BIG_BLIND = 100
RANK_VALUES = {rank: value for value, rank in enumerate('23456789TJQKA', start=2)}
AGGRESSIVE_MOVES = {Move.BET, Move.RAISE, Move.ALL_IN}
ALL_CARDS = tuple(rank + suit for suit in 'dhsc' for rank in '23456789TJQKA')

class MyPlayer(Player):
    name = 'Azalea'
    image_path = 'images/your_image.png'

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

    def opponent_profile(self) -> tuple[bool, bool]:
        if len(self.hands_shown) < 2:
            return False, False

        average_strength = sum(
            self.preflop_strength_for(cards)
            for cards, _ in self.hands_shown
        ) / len(self.hands_shown)

        return average_strength >= 0.68, average_strength <= 0.48

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
                wins += 1
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

    def preflop_move(
        self,
        valid_moves: list[Move],
        min_bet: int,
        max_bet: int,
        to_call: int,
        pot_odds: float,
        round_history: list[tuple[Move, int]],
        opp_tight: bool,
    ) -> tuple[Move, int] | Move:
        strength = self.preflop_strength()
        button_open = self.pot_commitment <= 50
        open_threshold = 0.3 if button_open else 0.42
        if opp_tight:
            open_threshold -= 0.04

        if to_call == 0:
            if strength >= 0.82:
                return self.aggressive_action(valid_moves, min_bet, max_bet, 350, Move.CHECK)
            if strength >= open_threshold:
                open_size = 250 if not round_history else max(300, min_bet)
                return self.aggressive_action(valid_moves, min_bet, max_bet, open_size, Move.CHECK)
            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        bet_size_bb = max(2.0, to_call / BIG_BLIND)
        stack_pressure = to_call / max(max_bet, 1)

        if strength >= 0.9 or (strength >= 0.82 and stack_pressure > 0.22):
            if Move.ALL_IN in valid_moves and max_bet <= 2200:
                return Move.ALL_IN
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.4))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        if strength >= 0.72 and bet_size_bb <= 6 and Move.RAISE in valid_moves:
            target = max(min_bet, int((to_call + self.pot_commitment) * 2.2))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

        defend_threshold = max(0.42, pot_odds + 0.12)
        if opp_tight and to_call <= BIG_BLIND:
            defend_threshold -= 0.04

        if strength >= defend_threshold and to_call <= max(700, int(max_bet * 0.18)) and Move.CALL in valid_moves:
            return Move.CALL

        return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

    def move(self, community_cards: list[str], valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        my_commitment, opp_commitment = self.commitments(round_history)
        to_call = max(0, opp_commitment - my_commitment)
        estimated_pot = self.estimate_pot(community_cards, my_commitment, opp_commitment)
        pot_odds = to_call / (estimated_pot + to_call) if to_call > 0 else 0.0
        opp_tight, opp_loose = self.opponent_profile()

        if not community_cards:
            return self.preflop_move(valid_moves, min_bet, max_bet, to_call, pot_odds, round_history, opp_tight)

        strength, strong_draw = self.postflop_strength(community_cards)
        aggression_count = sum(move in AGGRESSIVE_MOVES for move, _ in round_history)
        facing_heat = to_call > 0 and aggression_count >= 2

        if to_call > 0:
            if strength >= 0.78:
                target = max(min_bet, int(opp_commitment + max(to_call * 1.6, estimated_pot * 0.5)))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            if strength >= 0.62 and not facing_heat and Move.RAISE in valid_moves:
                target = max(min_bet, int(opp_commitment + max(to_call * 1.25, estimated_pot * 0.3)))
                return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CALL)

            call_threshold = pot_odds + 0.06
            if opp_loose:
                call_threshold -= 0.03
            if opp_tight:
                call_threshold += 0.02
            if facing_heat:
                call_threshold += 0.05

            if strong_draw and len(community_cards) < 5:
                call_threshold -= 0.05

            if strength >= call_threshold and to_call <= max(900, int(max_bet * 0.25)) and Move.CALL in valid_moves:
                return Move.CALL

            if strong_draw and to_call <= 250 and Move.CALL in valid_moves:
                return Move.CALL

            return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD

        if strength >= 0.72:
            target = max(min_bet, int(estimated_pot * 0.75))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if strength >= 0.58:
            target = max(min_bet, int(estimated_pot * 0.55))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if len(community_cards) == 3 and strength >= 0.44 and not opp_loose:
            target = max(min_bet, int(estimated_pot * 0.4))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if strong_draw and len(community_cards) < 5 and not opp_loose:
            target = max(min_bet, int(estimated_pot * 0.45))
            return self.aggressive_action(valid_moves, min_bet, max_bet, target, Move.CHECK)

        if Move.CHECK in valid_moves:
            return Move.CHECK
        return Move.FOLD

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

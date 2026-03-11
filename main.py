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
    name = 'Default Name 3'
    image_path = 'images/your_image.png' # Optional

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

    def move(self, community_cards: list[str], valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        """Your move code here! You are given the community cards (cards both players have access to, the objective is to use your 2 cards (self.cards) with the community cards to make the best 5-card poker hand).
        You are also given a list containing the legal moves you can currently make, for example, if the opponent has bet then you can only call, raise or fold but cannot check.
        If your bot attempts to make an illegal move it will fold its hand (forfeiting any chips already in the pot), so ensure not to do this."""

        
        # self.get_hand_type(community_cards) == HandRank.THREE_OF_A_KIND
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

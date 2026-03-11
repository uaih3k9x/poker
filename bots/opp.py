from phevaluator.evaluator import evaluate_cards
from logic import Move, Player, HandRank
import random

class MyPlayer(Player):
    name = 'Rando-Slayer-v2'

    def get_equity(self, community_cards: list[str], iterations: int = 400) -> float:
        """蒙特卡洛模拟：计算在当前已知牌面下，面对随机手牌的胜率"""
        all_cards = [r+s for r in "23456789TJQKA" for s in "shdc"]
        used_cards = set(community_cards + self.cards)
        deck = [c for c in all_cards if c not in used_cards]
        
        wins = 0
        for _ in range(iterations):
            random.shuffle(deck)
            needed = 5 - len(community_cards)
            sim_community = community_cards + deck[:needed]
            opp_cards = deck[needed:needed+2]
            
            my_score = evaluate_cards(*sim_community, *self.cards)
            opp_score = evaluate_cards(*sim_community, *opp_cards)
            
            if my_score < opp_score: # phevaluator 中越小越强
                wins += 1
            elif my_score == opp_score:
                wins += 0.5
        return wins / iterations

    def _preflop_strategy(self) -> float:
        """翻前简单的强度评估"""
        ranks = "23456789TJQKA"
        r1, r2 = sorted([ranks.index(c[0]) for c in self.cards], reverse=True)
        suited = self.cards[0][1] == self.cards[1][1]
        pair = r1 == r2
        
        score = r1 * 2 + r2 
        if pair: score += 25
        if suited: score += 12
        if (r1 - r2) <= 2: score += 5 # 连牌潜力
        
        return min(1.0, score / 55.0)

    def move(self, community_cards: list[str], valid_moves: list[Move], round_history: list[tuple[Move, int]], min_bet: int, max_bet: int) -> tuple[Move, int] | Move:
        street = len(community_cards)
        
        # 1. 获取胜率估算
        if street == 0:
            equity = self._preflop_strategy()
        else:
            equity = self.get_equity(community_cards)

        # 2. 计算底池赔率 (Pot Odds) 
        # 我们需要投入的筹码 / (当前池子总额 + 我们需要投入的筹码)
        # 这里简化处理：根据 min_bet 占 max_bet 的比例来判断
        call_cost_ratio = min_bet / max_bet if max_bet > 0 else 0

        # 3. 针对 RandomPlayer 的特殊打法：
        
        # --- 绝对强牌 (Equity > 75%) ---
        if equity > 0.75:
            # 随机玩家会跟注任何注码，所以我们直接下重注
            if Move.RAISE in valid_moves or Move.BET in valid_moves:
                # 下注 70% - 100% 的筹码
                bet_amount = int(min_bet + (max_bet - min_bet) * 0.8)
                return (Move.RAISE if Move.RAISE in valid_moves else Move.BET, bet_amount)
            if Move.ALL_IN in valid_moves:
                return Move.ALL_IN
            return Move.CALL if Move.CALL in valid_moves else Move.CHECK

        # --- 中等强牌 (Equity 50% - 75%) ---
        elif equity > 0.50:
            # 如果对手下注特别猛（例如直接推 All-in），随机玩家经常在诈唬，我们接！
            if Move.CALL in valid_moves:
                # 对付随机玩家，50% 以上胜率就可以接他的全压
                return Move.CALL
            if Move.CHECK in valid_moves:
                return Move.CHECK
            return Move.FOLD

        # --- 弱牌 (Equity 35% - 50%) ---
        elif equity > 0.35:
            # 只有在非常便宜的时候才看牌（底池赔率合算）
            if Move.CHECK in valid_moves:
                return Move.CHECK
            if Move.CALL in valid_moves and call_cost_ratio < 0.15: # 成本小于 15% 筹码才跟
                return Move.CALL
            return Move.FOLD

        # --- 垃圾牌 (Equity < 35%) ---
        else:
            if Move.CHECK in valid_moves:
                return Move.CHECK
            # 面对随机玩家，不要试图诈唬（Bluff），因为他们根本不看牌，会用垃圾牌跟你到底
            return Move.FOLD
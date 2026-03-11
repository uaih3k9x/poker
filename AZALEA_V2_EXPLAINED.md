# AzaleaV2 Explained

中英双语说明文档。  
Bilingual explanation document for `AzaleaV2`.

本文档解释 [main.py](/Users/uaih3k9x/poker/main.py) 和 [bots/azalea_v2.py](/Users/uaih3k9x/poker/bots/azalea_v2.py) 里的核心逻辑。两份文件现在是同一套 bot 实现。  
This document explains the core logic in [main.py](/Users/uaih3k9x/poker/main.py) and [bots/azalea_v2.py](/Users/uaih3k9x/poker/bots/azalea_v2.py). They currently contain the same bot implementation.

---

## 1. 一句话总结 | One-Sentence Summary

中文：  
`AzaleaV2` 是一个轻量级、偏 exploit 的 heads-up no-limit hold'em bot。它用启发式翻前牌力模型、小规模翻后 Monte Carlo 胜率估计、以及简单的 tight/loose 对手画像来做决策。

English:  
`AzaleaV2` is a lightweight, exploit-oriented heads-up no-limit hold'em bot. It combines a heuristic preflop hand model, small-scale postflop Monte Carlo equity estimation, and a simple tight/loose opponent profile.

---

## 2. 设计目标 | Design Goals

中文：

- 在严格时间限制下稳定运行
- 不依赖重型搜索或 solver
- 对常见 baseline bot 保持高胜率
- 保持逻辑可解释、可调参、可扩展

English:

- Run reliably under a strict time budget
- Avoid heavy search or solver-style computation
- Maintain strong win rates against common baseline bots
- Keep the logic understandable, tunable, and extensible

---

## 3. 顶层结构 | Top-Level Structure

中文：  
代码结构可以分成六层：

1. 常量和牌面编码
2. 翻前牌力估计
3. 对手画像
4. 当前街投入与底池估计
5. 翻后 equity 与听牌识别
6. 翻前 / 翻后决策逻辑

English:  
The code can be understood in six layers:

1. Constants and card encoding
2. Preflop hand-strength estimation
3. Opponent profiling
4. Per-street commitment and pot estimation
5. Postflop equity and draw detection
6. Preflop and postflop decision logic

---

## 4. 常量区在做什么 | What the Constants Do

代码位置：
- [main.py:10](/Users/uaih3k9x/poker/main.py#L10)
- [main.py:13](/Users/uaih3k9x/poker/main.py#L13)

```python
MATCHES = 1000
PARALLEL = False

BIG_BLIND = 100
RANK_VALUES = {rank: value for value, rank in enumerate("23456789TJQKA", start=2)}
AGGRESSIVE_MOVES = {Move.BET, Move.RAISE, Move.ALL_IN}
ALL_CARDS = tuple(rank + suit for suit in "dhsc" for rank in "23456789TJQKA")
```

中文：

- `BIG_BLIND = 100` 定义了 1bb 的尺度
- `RANK_VALUES` 负责把牌面字符变成数值
- `AGGRESSIVE_MOVES` 用来判断一条街是否已经变得激烈
- `ALL_CARDS` 是 Monte Carlo 抽样用的完整牌库

English:

- `BIG_BLIND = 100` defines the size of 1bb
- `RANK_VALUES` converts rank characters into numeric values
- `AGGRESSIVE_MOVES` is used to detect whether a street has become aggressive
- `ALL_CARDS` is the full deck used for Monte Carlo sampling

---

## 5. 类入口 | Class Entry Point

代码位置：
- [main.py:19](/Users/uaih3k9x/poker/main.py#L19)

```python
class MyPlayer(Player):
    name = "AzaleaV2"
```

中文：  
这个类继承比赛框架里的 `Player`，核心职责只有一个：每次轮到行动时，在 `move(...)` 中返回一个合法动作。

English:  
This class inherits from the framework's `Player` class. Its core job is simple: every time it acts, it returns a legal move from `move(...)`.

---

## 6. 基础工具函数 | Utility Helpers

### 6.1 `clamp`

代码位置：
- [main.py:23](/Users/uaih3k9x/poker/main.py#L23)

中文：  
把任意分数限制在给定范围内，通常是 `[0, 1]`。它防止手工加减权重后出现超出上界或下界的值。

English:  
This clamps any score into a fixed range, usually `[0, 1]`. It prevents heuristic adjustments from pushing values outside the intended bounds.

### 6.2 `ranks`

代码位置：
- [main.py:27](/Users/uaih3k9x/poker/main.py#L27)

中文：  
把牌字符串转换成 rank 数值列表，比如 `['As', 'Td']` 变成 `[14, 10]`。顺子听牌识别和牌面结构判断都依赖它。

English:  
This converts card strings into rank values, for example `['As', 'Td']` becomes `[14, 10]`. Straight-draw detection and board-structure reasoning depend on it.

---

## 7. 翻前牌力模型 | Preflop Hand-Strength Model

代码位置：
- [main.py:31](/Users/uaih3k9x/poker/main.py#L31)
- [main.py:57](/Users/uaih3k9x/poker/main.py#L57)

### 7.1 这个函数在干什么 | What This Function Does

`preflop_strength_for(cards)` 把两张底牌映射成一个 0 到 1 之间的强度分数。

`preflop_strength_for(cards)` maps a two-card starting hand into a strength score between 0 and 1.

### 7.2 它考虑的因素 | Features It Uses

中文：

- 是否是对子
- 是否同花
- 高牌质量
- 两张牌的 gap
- 是否是高张组合
- A-x 的 heads-up 价值

English:

- Whether the hand is a pair
- Whether the cards are suited
- High-card quality
- The gap between the two cards
- Whether the hand contains strong broadway-style cards
- The heads-up value of A-x hands

### 7.3 为什么对子单独处理 | Why Pairs Are Treated Separately

代码位置：
- [main.py:38](/Users/uaih3k9x/poker/main.py#L38)

```python
if r1 == r2:
    return MyPlayer.clamp(0.4 + high * 0.04, 0.48, 0.96)
```

中文：  
对子在 heads-up 中远比普通非对子有价值，所以这份 bot 不把对子和其他牌混在同一个公式里，而是直接给对子更高的基础权重。

English:  
Pairs are much stronger than ordinary unpaired hands in heads-up play, so this bot does not score them with the same formula. Instead, it gives them a much stronger baseline immediately.

### 7.4 非对子评分的直觉 | Intuition Behind the Unpaired-Hand Formula

代码位置：
- [main.py:41](/Users/uaih3k9x/poker/main.py#L41)

中文：  
这部分逻辑基本表达了经典扑克直觉：

- 高张更好
- 同花更好
- 连张更好
- 大 gap 更差
- 双高张更好
- A-x 常常在 heads-up 里有额外价值

English:  
This section encodes standard poker intuition:

- High cards are better
- Suited hands are better
- Connected hands are better
- Large gaps are worse
- Two high cards are stronger
- A-x usually carries extra heads-up value

### 7.5 为什么这套模型够用 | Why This Model Is Good Enough

中文：  
它不是完整的 GTO preflop chart，也不是数据库查表，但它速度极快，而且分层足够合理，能给后面的下注逻辑提供稳定输入。

English:  
It is not a full GTO chart and not a lookup-table engine, but it is extremely fast and produces a sensible hand ordering. That is enough to drive the rest of the decision logic.

---

## 8. 当前手牌强度入口 | Current-Hand Strength Wrapper

代码位置：
- [main.py:57](/Users/uaih3k9x/poker/main.py#L57)

```python
def preflop_strength(self) -> float:
    return self.preflop_strength_for(self.cards)
```

中文：  
这个函数只是把当前手牌传给通用模型，作用是减少后续调用时的重复代码。

English:  
This is just a small wrapper around the generic hand-strength function for the current hole cards. It exists mainly to keep later code cleaner.

---

## 9. 对手画像 | Opponent Profiling

代码位置：
- [main.py:60](/Users/uaih3k9x/poker/main.py#L60)

### 9.1 核心思路 | Core Idea

中文：  
这份 bot 不试图做复杂 HUD，也不识别对手名字。它只做一个很粗但很快的分类：

- 对手是偏紧的
- 对手是偏松的

English:  
This bot does not try to build a full HUD and does not identify opponents by name. It only does a simple and fast classification:

- The opponent is relatively tight
- The opponent is relatively loose

### 9.2 它用什么数据 | What Data It Uses

中文：  
它只看 `hands_shown`，也就是在摊牌时看到的对手底牌。然后用自己的翻前强度模型给这些牌打分，算平均值。

English:  
It only uses `hands_shown`, meaning the opponent's hole cards revealed at showdown. It then scores those hands using its own preflop model and averages the result.

### 9.3 为什么这样做 | Why This Works in Practice

中文：  
虽然这种方法有样本偏差，但它很便宜，也足够区分出两类常见对手：

- 只肯拿好牌进池的紧手
- 很多边缘牌也愿意继续的松手

English:  
Although this method is biased by showdown selection, it is cheap and still good enough to distinguish two common opponent types:

- Tight players who mostly continue with strong hands
- Loose players who continue with many marginal hands

### 9.4 局限 | Limitation

中文：  
它看不到对手 fold 掉的牌，所以这不是完整 range 估计。

English:  
It cannot see hands that the opponent folded, so this is not a full-range estimate.

---

## 10. 解析本街投入 | Reconstructing Per-Street Commitments

代码位置：
- [main.py:71](/Users/uaih3k9x/poker/main.py#L71)

### 10.1 `commitments(...)` 在做什么 | What `commitments(...)` Does

中文：  
这个函数扫描当前街的 `round_history`，恢复出：

- 我这一街已经总共投了多少
- 对手这一街已经总共投了多少

English:  
This function scans the current street's `round_history` and reconstructs:

- how much I have committed on this street
- how much the opponent has committed on this street

### 10.2 为什么必须自己算 | Why the Bot Has to Infer This

中文：  
因为 bot 的输入不是完整桌面状态，它必须从历史动作恢复出当前要补多少钱、现在压力多大、底池大概有多大。

English:  
Because the bot does not receive a perfect full-table state, it has to reconstruct how much it must call, how much pressure it faces, and roughly how large the pot is from the betting history.

---

## 11. 底池估计 | Pot Estimation

代码位置：
- [main.py:86](/Users/uaih3k9x/poker/main.py#L86)

```python
def estimate_pot(self, community_cards, my_commitment, opp_commitment) -> int:
    baseline = 0 if not community_cards else 200 + max(0, len(community_cards) - 3) * 50
    return baseline + my_commitment + opp_commitment
```

中文：  
这里不是精确底池恢复，而是一个启发式近似值。它的目的是给 pot odds 和 bet sizing 提供一个稳定的量级估计。

English:  
This is not a perfect pot reconstruction. It is a heuristic estimate whose purpose is to provide a stable scale for pot-odds calculations and bet sizing.

### 11.1 好处 | Strength

中文：  
计算极快，而且在这份 bot 的参数体系下足够稳定。

English:  
It is extremely fast and stable enough for the rest of the bot's threshold system.

### 11.2 缺点 | Weakness

中文：  
它不是真正的底池会计，因此某些 turn/river 或多次 raise 的场景会有偏差。

English:  
It is not true pot accounting, so it can be inaccurate on some turn/river spots or after multiple raises.

---

## 12. 顺子听牌识别 | Straight-Draw Detection

代码位置：
- [main.py:90](/Users/uaih3k9x/poker/main.py#L90)

中文：  
`straight_draw_flags(...)` 用枚举 5 连张窗口的方法判断：

- 是否是 open-ended straight draw
- 是否是 gutshot

English:  
`straight_draw_flags(...)` checks possible five-rank windows to determine:

- whether the hand has an open-ended straight draw
- whether it has a gutshot draw

### 为什么要分开 | Why the Split Matters

中文：  
两头顺明显强于 gutshot，后面翻后逻辑会更积极地继续强听牌。

English:  
An open-ended straight draw is significantly stronger than a gutshot, so later postflop logic continues with strong draws more aggressively.

---

## 13. 翻后胜率估计 | Postflop Equity Estimation

代码位置：
- [main.py:111](/Users/uaih3k9x/poker/main.py#L111)

### 13.1 核心方法 | Core Method

中文：  
`postflop_strength(...)` 通过小规模 Monte Carlo 来估算当前胜率。每次采样都会：

- 随机给对手两张牌
- 随机补完后续公共牌
- 比较双方最终牌力

English:  
`postflop_strength(...)` estimates current equity with a small Monte Carlo simulation. Each sample:

- deals two random hole cards to the opponent
- completes the future board randomly
- compares final hand strength

### 13.2 采样数为什么按街变化 | Why Sample Count Depends on Street

代码位置：
- [main.py:121](/Users/uaih3k9x/poker/main.py#L121)

```python
samples = {3: 96, 4: 72, 5: 48}[len(community_cards)]
```

中文：  
翻牌圈不确定性最大，所以采样更多。河牌接近确定，所以采样更少。这是一种典型的“把算力放在最需要的位置”的策略。

English:  
The flop has the most uncertainty, so it uses more samples. The river is nearly resolved, so it uses fewer. This is a classic way to spend computation where it matters most.

### 13.3 为什么同时返回 `strong_draw` | Why It Also Returns `strong_draw`

中文：  
因为当前 equity 不是全部。很多牌现在不领先，但改进空间大，比如同花听牌和两头顺。这个信号会在跟注和激进反打时单独使用。

English:  
Because current equity is not the whole story. Some hands are not currently ahead but have strong improvement potential, such as flush draws and open-ended straight draws. That signal is used separately in calling and aggressive semibluff logic.

---

## 14. 激进行为封装器 | Aggressive Action Wrapper

代码位置：
- [main.py:142](/Users/uaih3k9x/poker/main.py#L142)

中文：  
`aggressive_action(...)` 是一个工程上的小抽象。上层逻辑只需要给出目标下注额和 fallback 行为，这个函数负责：

- 把下注额夹在 `[min_bet, max_bet]`
- 优先选择 `RAISE`
- 否则选择 `BET`
- 必要时退回 `CHECK`、`CALL` 或 `FOLD`

English:  
`aggressive_action(...)` is a small engineering abstraction. Higher-level logic only specifies a target size and a fallback action. This helper:

- clamps the size into `[min_bet, max_bet]`
- prefers `RAISE`
- otherwise uses `BET`
- falls back to `CHECK`, `CALL`, or `FOLD` when necessary

### 为什么这很有用 | Why This Is Useful

中文：  
它把“策略上想打多大”和“规则上能不能这么打”分开了，所以上层逻辑更干净。

English:  
It separates "what size the strategy wants" from "what size is legal in the current state", which keeps higher-level decision code cleaner.

---

## 15. 翻前决策树 | Preflop Decision Tree

代码位置：
- [main.py:163](/Users/uaih3k9x/poker/main.py#L163)

### 15.1 无人下注时 | When No Call Is Required

中文：  
如果 `to_call == 0`，bot 会决定是否主动开池。

关键逻辑：

- 如果自己更像 button，小盲位，则开池阈值更低
- 如果是很强的牌，直接用更大 sizing
- 中强牌标准开池
- 太弱的牌 check 或 fold

English:  
If `to_call == 0`, the bot decides whether to open the action.

Key logic:

- If it is effectively on the button / small blind, it opens wider
- Very strong hands use a larger sizing
- Medium-strength hands use a standard opening size
- Weak hands check or fold

### 15.2 为什么 button 开得更宽 | Why the Button Opens Wider

代码位置：
- [main.py:174](/Users/uaih3k9x/poker/main.py#L174)

中文：  
因为 heads-up 里位置价值非常大。button 翻后更多时候能后手行动，所以可以承担更宽的开池范围。

English:  
Because position is extremely valuable in heads-up play. The button acts later postflop more often, so it can profitably open a wider range.

### 15.3 面对下注时 | When Facing a Bet

中文：  
翻前面对下注时，bot 把牌大致分成三层：

- 顶级牌：高压反击，甚至全下
- 较强牌：在合理尺寸下再加注
- 中档牌：按 pot odds 和阈值防守

English:  
When facing a bet preflop, the bot roughly splits hands into three groups:

- Premium hands: apply heavy pressure, sometimes all-in
- Strong hands: reraise at reasonable sizes
- Medium hands: defend based on thresholds and pot odds

### 15.4 `stack_pressure` 的意义 | Meaning of `stack_pressure`

代码位置：
- [main.py:188](/Users/uaih3k9x/poker/main.py#L188)

中文：  
它衡量当前补注相对于自己可投入总额的压力。如果压力已经很高，强牌会更愿意直接打大。

English:  
It measures how expensive the call is relative to the maximum amount the bot can still commit. When pressure is already high, strong hands become more willing to play for bigger pots immediately.

---

## 16. 翻后总入口 | Postflop Entry Logic

代码位置：
- [main.py:209](/Users/uaih3k9x/poker/main.py#L209)

中文：  
一旦有公共牌，代码会先计算：

- `to_call`
- `estimated_pot`
- `pot_odds`
- `strength`
- `strong_draw`
- `facing_heat`

这些变量构成翻后所有决策的基础。

English:  
Once there are community cards, the code first computes:

- `to_call`
- `estimated_pot`
- `pot_odds`
- `strength`
- `strong_draw`
- `facing_heat`

These values form the basis for all postflop decisions.

---

## 17. 翻后面对下注 | Postflop When Facing a Bet

代码位置：
- [main.py:230](/Users/uaih3k9x/poker/main.py#L230)

### 17.1 强牌：重价值线 | Strong Hands: Heavy Value Line

```python
if strength >= 0.78:
```

中文：  
如果 equity 已经明显领先，bot 会主动加注，而不是只跟注。目的很直接：把优势转化成更大的期望收益。

English:  
If equity is clearly ahead, the bot raises rather than merely calling. The purpose is straightforward: convert hand advantage into larger expected value.

### 17.2 中高强度牌：低压下反打 | Medium-Strong Hands: Push Back Under Lower Pressure

```python
if strength >= 0.68 and not facing_heat and len(community_cards) < 5 and Move.RAISE in valid_moves:
```

中文：  
这条线表达的是：

- 牌不差
- 局面还没过热
- 还不是 river

这时可以主动反打，争取价值和 fold equity。

English:  
This line says:

- the hand is solid
- the spot is not too heated
- and it is not yet the river

In those cases, the bot can push back to capture both value and fold equity.

### 17.3 跟注阈值 | Calling Threshold

代码位置：
- [main.py:239](/Users/uaih3k9x/poker/main.py#L239)

中文：  
`call_threshold = pot_odds + 0.09` 是这份 bot 最重要的防守线之一。它不是单纯看 equity，也不是单纯看 pot odds，而是在 pot odds 之上加一层安全边际。

English:  
`call_threshold = pot_odds + 0.09` is one of the bot's most important defensive rules. It does not rely purely on equity or purely on pot odds. Instead, it adds a safety margin on top of pot odds.

### 17.4 对手画像如何影响跟注线 | How Opponent Type Adjusts the Calling Line

代码位置：
- [main.py:240](/Users/uaih3k9x/poker/main.py#L240)

中文：

- 对松手：略微放宽
- 对紧手：略微收紧
- 面对高压：明显收紧
- 有强听牌：适度放宽

English:

- Against loose opponents: call a bit wider
- Against tight opponents: call a bit tighter
- Under heavy aggression: tighten significantly
- With strong draws: continue somewhat wider

### 17.5 听牌兜底规则 | Draw Safety Net

代码位置：
- [main.py:253](/Users/uaih3k9x/poker/main.py#L253)

```python
if strong_draw and to_call <= 150 and Move.CALL in valid_moves:
    return Move.CALL
```

中文：  
即便主阈值没完全满足，只要价格足够便宜，强听牌也不轻易丢掉。

English:  
Even if the main threshold is not fully met, the bot still keeps strong draws when the price is cheap enough.

---

## 18. 翻后无人下注时 | Postflop When Checked To

代码位置：
- [main.py:258](/Users/uaih3k9x/poker/main.py#L258)

### 18.1 强牌主动下重注 | Strong Hands Bet Big

中文：  
`strength >= 0.78` 时，bot 下注约 `0.85 * estimated_pot`。这是很典型的重价值下注。

English:  
When `strength >= 0.78`, the bot bets about `0.85 * estimated_pot`. This is a classic heavy value-bet line.

### 18.2 中等牌也不被动 | Medium Hands Are Not Passive

中文：  
`strength >= 0.6` 时，bot 仍然会主动下注。这样可以避免白送免费牌，也能赢到很多没走到摊牌的小池。

English:  
When `strength >= 0.6`, the bot still bets proactively. This avoids giving away free cards and helps win many pots that never reach showdown.

### 18.3 Flop 主动 stab | Flop Stabs

代码位置：
- [main.py:266](/Users/uaih3k9x/poker/main.py#L266)

中文：  
在 flop 上，如果牌力尚可且对手不像 loose 玩家，bot 会主动打一枪。这能积累大量小型无摊牌收益。

English:  
On the flop, if the hand has reasonable strength and the opponent is not especially loose, the bot makes a proactive stab. This generates a lot of small non-showdown profit.

### 18.4 强听牌的主动进攻 | Strong Draws Can Attack

代码位置：
- [main.py:270](/Users/uaih3k9x/poker/main.py#L270)

中文：  
强听牌不只是被动跟注。bot 也会在合适场景主动下注，让自己的范围更难被读，同时制造 fold equity。

English:  
Strong draws are not only passive calling hands. The bot can also bet them in suitable spots, making its range harder to read while generating fold equity.

---

## 19. 这份 bot 的整体风格 | Overall Style of the Bot

中文：  
`AzaleaV2` 的风格可以概括为：

- 翻前比原始 `Azalea` 更 aggressive
- 翻后有更重的 value bet
- 对边缘牌和高压局面更克制
- 对强听牌保留继续和反打能力

English:  
The style of `AzaleaV2` can be summarized as:

- More aggressive preflop than the original `Azalea`
- Heavier value betting postflop
- More discipline with marginal hands under pressure
- Keeps both calling and semibluffing options with strong draws

---

## 20. 为什么它能赢 | Why It Wins

中文：

1. 翻前手牌分层清晰，不会把很多边缘牌玩错
2. 翻后不是死看 equity，而是把 pot odds、压力和听牌一起考虑
3. 强牌价值线更重，能把领先转换成更多筹码
4. 中等牌不会无脑打大池，降低了被反吃的频率

English:

1. Its preflop hand tiers are sensible, so it avoids many common marginal-hand mistakes
2. Postflop logic combines equity, pot odds, pressure, and draw quality instead of using equity alone
3. Its value lines are heavy enough to convert strong hands into real chip gain
4. It avoids bloating pots too often with medium-strength hands, reducing expensive mistakes

---

## 21. 已知局限 | Known Limitations

中文：

- `estimate_pot(...)` 是启发式，不是真实底池恢复
- `opponent_profile(...)` 只有 `tight / loose` 两档，粒度偏粗
- 没有显式建模 board texture 的更多细节
- Monte Carlo 本质上会有采样噪声

English:

- `estimate_pot(...)` is heuristic, not true pot reconstruction
- `opponent_profile(...)` only has a coarse `tight / loose` split
- It does not explicitly model richer board-texture classes
- Monte Carlo estimation inherently contains sampling noise

---

## 22. 如果你要口头解释这份代码 | How to Explain This Bot Verbally

中文短版：

> AzaleaV2 是一个轻量级 exploit bot。翻前我用启发式手牌模型快速给起手牌分层，翻后用小规模 Monte Carlo 算当前胜率，再结合对手是偏紧还是偏松来动态调整跟注、再加注和价值下注阈值。它不是靠重型搜索取胜，而是靠稳定、快速和实用的决策逻辑取胜。

English short version:

> AzaleaV2 is a lightweight exploitative bot. Preflop, it uses a heuristic hand model to tier starting hands quickly. Postflop, it estimates equity with a small Monte Carlo simulation and then adjusts calling, reraising, and value-betting thresholds based on whether the opponent looks tighter or looser. It does not win through heavy search, but through stable, fast, and practical decision logic.

---

## 23. 术语速查 | Quick Glossary

### 23.1 `1bb`

中文：  
`1bb` 就是 `1 big blind`。在这个项目里，`1bb = 100` 筹码。

English:  
`1bb` means `1 big blind`. In this project, `1bb = 100` chips.

### 23.2 `pot`

中文：  
`pot` 是当前手牌桌面中间已经投入、等待最终赢家拿走的总筹码。

English:  
The `pot` is the total amount of chips currently in the middle of the table, waiting to be awarded to the final winner of the hand.

### 23.3 `pot odds`

中文：  
`pot odds` 是你为了继续这手牌需要投入的成本，相对于你可能赢到的总池子大小的比例。

English:  
`pot odds` is the ratio between the cost of continuing in the hand and the total pot you can win if you continue.

### 23.4 `equity`

中文：  
`equity` 是在当前已知信息下，这手牌最终获胜的概率估计。

English:  
`equity` is the estimated probability that the hand will eventually win given the currently known information.

---

## 24. 最后的评价 | Final Assessment

中文：  
`AzaleaV2` 不是理论最优 bot，也不是最复杂的 bot。但它是一个非常像样的比赛 bot：结构清晰、运行很快、逻辑可解释、参数容易调，而且在常见对手池里确实能打。

English:  
`AzaleaV2` is not a theoretically optimal bot and not the most complicated bot. But it is a very solid competition bot: the structure is clean, it runs quickly, the logic is explainable, the parameters are easy to tune, and it performs well in a practical opponent pool.

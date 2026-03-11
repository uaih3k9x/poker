# AzaleaV2 代码详解

本文档解释 [bots/azalea_v2.py](/Users/uaih3k9x/poker/bots/azalea_v2.py) 的设计思路、函数职责、决策流程、策略风格，以及它为什么能在当前 bot 池中取得不错表现。

这份说明不是简单的“逐行翻译”，而是按“设计目标 -> 数据流 -> 决策逻辑 -> 优缺点”的方式来讲。这样你后面不管是答辩、演讲、继续改 bot，还是让别人接手，都更容易理解。

---

## 1. 这个 bot 想解决什么问题

`AzaleaV2` 是一个典型的 **轻量级 exploitative heads-up poker bot**。

它没有走重型 solver 的路线，而是选了更适合比赛环境的一条路：

1. 翻前用一个很快的启发式模型评估两张底牌强度。
2. 翻后用小规模 Monte Carlo 估计当前 equity。
3. 用少量对手建模信息，判断对手更像紧手还是松手。
4. 基于阈值、pot odds、下注压力和听牌质量来做决策。

换句话说，这个 bot 的核心哲学不是“理论最优”，而是：

- 足够快
- 足够稳
- 足够适合 exploit 常见 bot

这也是它在当前对局池里表现不错的原因。

---

## 2. 文件整体结构

`bots/azalea_v2.py` 可以分成 6 个部分：

1. 常量定义
2. 基础工具函数
3. 翻前牌力模型
4. 对手画像与局面解析
5. 翻后胜率估计
6. 主决策逻辑

再加一个文件底部的 `run_match` / `__main__`，用于本地自测。

---

## 3. 常量层：这份 bot 的“全局假设”

文件开头定义了几个非常重要的常量：

```python
MATCHES = 1000
PARALLEL = False

BIG_BLIND = 100
RANK_VALUES = {rank: value for value, rank in enumerate("23456789TJQKA", start=2)}
AGGRESSIVE_MOVES = {Move.BET, Move.RAISE, Move.ALL_IN}
ALL_CARDS = tuple(rank + suit for suit in "dhsc" for rank in "23456789TJQKA")
```

这些常量决定了 bot 的“世界观”：

- `BIG_BLIND = 100`：所有翻前 sizing 和压力判断都默认用 100 作为 1BB。
- `RANK_VALUES`：把牌面字符映射成数值，方便算高牌、间隔、连张。
- `AGGRESSIVE_MOVES`：定义什么动作算主动进攻，用来统计场面激烈程度。
- `ALL_CARDS`：完整牌库，供 Monte Carlo 抽样时使用。

这里有一个很关键的设计点：  
这份 bot 大量逻辑都在用 **big blind 视角** 思考，而不是直接用“绝对筹码值”思考。这样更稳定，也更符合扑克分析习惯。

---

## 4. 类的入口：`MyPlayer`

```python
class MyPlayer(Player):
    name = "AzaleaV2"
    image_path = "images/your_image.png"
```

这部分本身不复杂，但有两个作用：

1. 告诉比赛框架，这个 bot 的名字是 `AzaleaV2`
2. 表明这个 bot 的所有行为由 `move(...)` 决定

真正重要的是：所有上层框架每次轮到它行动时，都会调用它的 `move(...)`。

---

## 5. 工具函数层

### 5.1 `clamp`

```python
def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
```

作用很直接：把数值限制在 `[low, high]` 之间。

为什么重要：

- 牌力分数最终都希望落在 `[0, 1]`
- Monte Carlo 结果有时会被额外加一点 bonus
- 如果不截断，后面的阈值判断容易失真

这是一个很小但很必要的稳定器。

### 5.2 `ranks`

```python
def ranks(cards: list[str]) -> list[int]:
    return [RANK_VALUES[card[0]] for card in cards]
```

作用是把 `['As', 'Td']` 这样的字符串牌表示法，转成 `[14, 10]` 这种数值表示。

它主要给两类逻辑服务：

- 顺子听牌识别
- 牌面结构分析

---

## 6. 翻前牌力模型：`preflop_strength_for`

这是整份 bot 最关键的基础函数之一。

```python
def preflop_strength_for(cards: list[str]) -> float:
```

它做的事情是：  
把两张底牌映射成一个 0 到 1 之间的强度分数。

### 6.1 模型考虑了什么

它主要考虑 5 类因素：

1. 是否是对子
2. 是否同花
3. 两张牌谁更高
4. 两张牌之间的 gap
5. 是否是高张组合，尤其是 Broadway 区域和 A-x

### 6.2 对子的特殊待遇

```python
if r1 == r2:
    return MyPlayer.clamp(0.4 + high * 0.04, 0.48, 0.96)
```

对子的逻辑是独立处理的，因为扑克里对子是翻前最特殊的一类手牌。

这个式子的含义很简单：

- 小对子不会太弱
- 大对子会很强
- AA、KK 这种牌会接近上界

也就是说，bot 直接假设：

- 对子天生有很高的基本赢率
- 不应该和普通非对子用同一套公式处理

### 6.3 非对子部分

```python
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
```

这段逻辑表达了典型的 heads-up preflop 常识：

- 高牌比低牌好
- 同花比不同花好
- 连张比断张好
- 双高张比一高一低更好
- A-x 在 heads-up 中通常有额外价值

这里没有试图实现完整的 GTO preflop chart，而是实现了一套：

- 非常快
- 足够合理
- 易于调参

的启发式强度函数。

### 6.4 为什么这种方法有效

因为在这种比赛环境里，翻前不需要极高精度，只需要：

- 把特别强的牌识别出来
- 把边缘牌分层
- 让后面的下注阈值有一个可以信赖的输入

这份函数已经足够做到这一点。

---

## 7. 单手牌力与对手画像

### 7.1 `preflop_strength`

```python
def preflop_strength(self) -> float:
    return self.preflop_strength_for(self.cards)
```

这是个很薄的封装，用当前手牌调用通用强度函数。

它的价值在于：后续逻辑不需要一遍遍传 `self.cards`。

### 7.2 `opponent_profile`

```python
def opponent_profile(self) -> tuple[bool, bool]:
```

这个函数通过 `self.hands_shown` 来估计对手更像：

- `tight`
- `loose`

核心做法是：

1. 只看摊牌时看到的对手手牌
2. 用自己的 `preflop_strength_for(...)` 给这些牌打分
3. 算平均值
4. 根据阈值判断偏紧还是偏松

```python
return average_strength >= 0.68, average_strength <= 0.48
```

也就是说：

- 对手展示的底牌平均偏强 -> 当成 tight
- 对手展示的底牌平均偏弱 -> 当成 loose

### 7.3 这方法的优点

- 计算极快
- 符合比赛合法信息范围
- 能粗略区分“只进好牌的人”和“啥都玩的人”

### 7.4 这方法的局限

它有明显偏差：

- 只能看见摊牌信息，看不见对手 fold 掉的牌
- 样本少的时候不稳定
- 它测的是“对手展示出的底牌质量”，不是“对手的真实全范围”

但在这个比赛环境里，它依然是一个实用的低成本画像器。

---

## 8. 解析局面：谁投了多少钱

### 8.1 `commitments`

```python
def commitments(self, round_history: list[tuple[Move, int]]) -> tuple[int, int]:
```

这个函数做的事情是：

- 扫描当前街的 `round_history`
- 推出“我这一街总共投到了多少”
- 推出“对手这一街总共投到了多少”

输出：

- `my_commitment`
- `opp_commitment`

### 8.2 为什么要自己算这个

因为 bot 的决策高度依赖：

- 当前需要补多少钱 `to_call`
- 当前池子大概有多大
- 对手是不是在给你施压

这些信息都必须从 betting history 里恢复出来。

### 8.3 实现技巧

```python
actor_is_me = len(round_history) % 2 == 0
```

这个小技巧用来推断“历史中的第一个动作是谁做的”，然后通过轮流翻转布尔值来恢复行动顺序。

虽然不花哨，但非常省计算量。

---

## 9. 底池估计：`estimate_pot`

```python
def estimate_pot(self, community_cards, my_commitment, opp_commitment) -> int:
    baseline = 0 if not community_cards else 200 + max(0, len(community_cards) - 3) * 50
    return baseline + my_commitment + opp_commitment
```

这部分不是“真实底池恢复”，而是一个 **启发式底池近似器**。

它假设：

- 翻牌前 baseline 为 0
- 翻牌后起始底池大约 200
- turn 和 river 每多一张公共牌，再补一个固定量

然后再加上当前街双方的 commitment。

### 9.1 为什么这么写

因为 bot 真正需要的不是一个绝对精确的底池值，而是一个 **能大体反映 pot size 的量级估计**。

只要这个估计：

- 单调合理
- 不严重失真
- 能支撑 pot odds 和 bet sizing

那就足够服务决策。

### 9.2 局限

这是 `V2` 里最明显的近似项之一。

它的问题是：

- turn / river 不一定真的对应这个固定增量
- 多次 raise 的街里会存在偏差
- 它更像“可用估值”，不是“精确会计”

不过这份 bot 的阈值和 sizing 逻辑就是围绕这个近似调出来的，所以实战里仍然有效。

---

## 10. 听牌检测：`straight_draw_flags`

```python
def straight_draw_flags(self, cards: list[str]) -> tuple[bool, bool]:
```

这个函数判断两件事：

- 有没有 open-ended straight draw
- 有没有 gutshot straight draw

### 10.1 它怎么做

1. 先把所有牌的 rank 放进集合
2. 如果有 A，就额外把 `1` 也放进去
3. 枚举所有可能的 5 连张窗口
4. 看窗口内是否正好中了 4 张
5. 如果缺的是两端之一，就是 open-ended
6. 如果缺的是中间，就是 gutshot

这个实现简单但很实用。

### 10.2 为什么要分 open-ended 和 gutshot

因为两者强度不同：

- open-ended 听牌更强
- gutshot 更弱

后面在翻后决策里，bot 会把“强听牌”看得更积极。

---

## 11. 翻后胜率估计：`postflop_strength`

这是整份 bot 的第二个核心函数。

```python
def postflop_strength(self, community_cards: list[str]) -> tuple[float, bool]:
```

它输出两个东西：

1. `strength`：当前胜率估计
2. `strong_draw`：当前是否持有强听牌

### 11.1 Equity 估计方式

核心做法是小规模 Monte Carlo：

```python
for _ in range(samples):
    draw = random.sample(remaining, 2 + cards_needed)
```

每次随机做三件事：

- 给对手发两张随机底牌
- 补齐未来公共牌
- 比较双方最终牌力

最后统计：

- 赢记 1
- 平局记 0.5

然后取平均，得到当前 equity。

### 11.2 为什么 sample 数量按街变化

```python
samples = {3: 96, 4: 72, 5: 48}[len(community_cards)]
```

含义是：

- flop 不确定性最大，所以采样更多
- turn 次之
- river 结果更接近确定，所以采样更少

这是一个很好的时间优化：

- 复杂的时候多算一点
- 简单的时候少算一点

### 11.3 为什么还要返回 `strong_draw`

因为 equity 不是全部。

有些牌：

- 当前成牌不强
- 但有很高的改进潜力

比如：

- 同花听牌
- 两头顺

这些牌在面对下注时，通常值得继续玩。

所以 bot 不只关心“当前多大概率赢”，也关心“未来有没有足够好的提升空间”。

### 11.4 额外加成

```python
if strong_draw and gutshot:
    strength += 0.02
```

这是一点手工 bias。

本质上是在表达：

- 如果你已经有强听牌
- 同时还有额外的顺子中洞可能

那这手牌的实战可玩性通常会略高于原始 equity 数字。

---

## 12. 动作封装器：`aggressive_action`

```python
def aggressive_action(...):
```

这是一个很重要的工程化函数。

它统一处理激进行为：

- 目标下注不能低于 `min_bet`
- 目标下注不能高于 `max_bet`
- 如果目标已经碰顶并且允许 `ALL_IN`，直接全压
- 优先 `RAISE`
- 其次 `BET`
- 如果都不合法，退回 fallback

### 12.1 为什么这个函数重要

因为它把“策略想要打多大”和“规则允许怎么打”分开了。

上层逻辑只要说：

- 我想打到多少
- 如果不行我想退回什么动作

底层统一处理合法性。

这是很典型的“把策略层和执行层分离”的好写法。

---

## 13. 翻前逻辑：`preflop_move`

这是 `V2` 的第一大决策核心。

### 13.1 第一层：是否无人下注

```python
if to_call == 0:
```

如果当前不用补注，bot 会优先判断是否主动开池。

关键变量：

```python
button_open = self.pot_commitment <= 50
open_threshold = 0.32 if button_open else 0.48
```

含义是：

- 如果自己像 button，小盲位，开池阈值更低
- 如果不是 button，开池阈值更高

这个设定反映的是 heads-up 一个基本原则：

- button 位置更值钱
- in position 可以开更宽

### 13.2 强牌怎么打

```python
if strength >= 0.84:
    return ... 400
```

强牌直接打大。

这部分策略思想很明确：

- 强牌不只要玩
- 还要尽量 build pot

### 13.3 中等可玩牌怎么打

```python
if strength >= open_threshold:
    open_size = 250 if not round_history else max(325, min_bet)
```

中档牌通常也主动打，但 sizing 更克制。

这类手牌主要目标是：

- 拿主动权
- 赢小池
- 逼对手用更差牌继续

### 13.4 无人下注但牌太差

```python
return Move.CHECK if Move.CHECK in valid_moves else Move.FOLD
```

如果能免费看牌就看，不强求硬打。

---

## 14. 翻前面对下注：防守 / 反击逻辑

### 14.1 超强牌：直接高压

```python
if strength >= 0.9 or (strength >= 0.84 and stack_pressure > 0.18):
```

当牌非常强，或者牌够强且下注已经带来明显筹码压力时：

- 能全下就全下
- 否则大幅再加注

这是一种非常 exploitative 的打法：

- 对弱 bot 非常有效
- 对宽范围开池者也有压制力

### 14.2 较强但没到顶的牌

```python
if strength >= 0.66 and bet_size_bb <= 6.5 and Move.RAISE in valid_moves:
```

这类牌会选择再加注，但前提是：

- 对手下注尺寸别太大

它体现的是一种非常实用的策略：

- 对可承受尺寸积极反击
- 对超大压力不乱接

### 14.3 中等牌的防守阈值

```python
defend_threshold = max(0.46, pot_odds + 0.14)
```

这句是 `V2` 的一个关键风格特征。

意思是：

- 不只看牌力
- 还看数学上的 pot odds
- 但整体防守线偏紧

再往下：

```python
if opp_tight and to_call <= BIG_BLIND:
    defend_threshold -= 0.03
```

这里略有一点 exploit：

- 如果对手偏紧，但现在只需要补很小的成本
- 那允许自己稍微多 defend 一点

### 14.4 这份翻前策略的总体风格

可以概括为：

- button 主动开得比较宽
- 面对下注时，更倾向于“强牌强打、中牌偏收”
- 这比原始 `Azalea` 更 aggressive
- 也是它胜率提升的重要原因之一

---

## 15. 主入口：`move`

`move(...)` 是整份 bot 的总调度器。

它的流程可以总结成：

1. 解析当前投入
2. 计算 `to_call`
3. 估算底池
4. 算 pot odds
5. 得到对手画像
6. 如果还在翻前，走 `preflop_move`
7. 否则走翻后逻辑

换句话说，`move` 本身不是在做复杂判断，而是在组织所有信息流。

---

## 16. 翻后逻辑：面对下注

### 16.1 先判断局面是否高压

```python
aggression_count = sum(move in AGGRESSIVE_MOVES for move, _ in round_history)
facing_heat = to_call > 0 and aggression_count >= 2
```

这表示：

- 如果当前这条街已经出现了多次激进动作
- 同时你还需要补注

那这是一个高压局面。

这个标记非常重要，因为很多 bot 的大失误就是：

- 在高压局面里还把中等牌当成 value hand 打

而 `V2` 会主动收紧。

### 16.2 顶级强牌：大做价值

```python
if strength >= 0.78:
    target = ...
```

当 equity 已经明显领先时：

- 不只是跟
- 而是主动把底池做大

这里的 sizing 比原版更重：

- `to_call * 1.8`
- 或者 `estimated_pot * 0.65`

这是非常明确的 value-maximization 思路。

### 16.3 中高强度牌：适度反打

```python
if strength >= 0.68 and not facing_heat and len(community_cards) < 5 and Move.RAISE in valid_moves:
```

这部分的前提更多：

- 牌不差
- 局面没有过热
- 还没到 river

为什么要这样写：

- flop / turn 还保留后续提升空间
- river 再加注更容易被强牌 snap-call
- 高压局面下中牌再打大池容易出事

这是一种“能压就压，但不乱压”的平衡设计。

### 16.4 跟注阈值

```python
call_threshold = pot_odds + 0.09
```

这是 `V2` 翻后最有个性的地方之一。

它不是：

- 只看 equity

也不是：

- 只看 pot odds

而是：

- 以 pot odds 为底
- 再加一层安全边际

后面再按对手类型和局面继续调整：

```python
if opp_loose:
    call_threshold -= 0.02
if opp_tight:
    call_threshold += 0.03
if facing_heat:
    call_threshold += 0.06
if strong_draw and len(community_cards) < 5:
    call_threshold -= 0.03
```

这段逻辑的含义非常符合实战：

- 对松手，稍微多抓一点
- 对紧手，尊重一点
- 对多次激烈行动，明显收紧
- 对强听牌，放宽继续门槛

### 16.5 听牌的最低保留线

```python
if strong_draw and to_call <= 150 and Move.CALL in valid_moves:
    return Move.CALL
```

这是一条非常实用的兜底规则：

- 即使 equity 估计没完全过阈值
- 但如果价格足够便宜
- 强听牌还是值得继续

这是防止 bot 因为采样误差或阈值偏差，把好 draw 过度弃掉。

---

## 17. 翻后逻辑：无人下注时怎么打

### 17.1 强牌主动打大

```python
if strength >= 0.78:
    target = max(min_bet, int(estimated_pot * 0.85))
```

这是标准 value line：

- 自己很可能领先
- 那就主动把价值榨出来

### 17.2 中等牌也继续压

```python
if strength >= 0.6:
    target = max(min_bet, int(estimated_pot * 0.62))
```

这说明 `V2` 不是个被动等摊牌的 bot。

它的想法是：

- 很多中等领先牌，如果你不主动下注，就会白送对手免费看牌

### 17.3 flop 的主动 stab

```python
if len(community_cards) == 3 and strength >= 0.52 and not opp_loose:
```

这条线在策略上很重要。

它表示：

- 到了 flop
- 如果牌还可以
- 而对手又不像特别乱来的 loose 玩家
- 可以用中等 sizing 抢主动

这能帮 bot 赢很多没有走到摊牌的小池。

### 17.4 强听牌也会主动下注

```python
if strong_draw and len(community_cards) < 5 and not opp_loose:
```

这条线背后的思想是：

- 听牌不只是被动 call
- 有些听牌适合主动下注制造 fold equity

这让 bot 的范围更不透明，也更难被 exploit。

---

## 18. 为什么 `V2` 有效

`AzaleaV2` 表现好的原因，不是某一个神奇 trick，而是几件事一起成立：

### 18.1 翻前分层清晰

它不是简单地“强牌打，弱牌弃”，而是把手牌分成：

- 顶级价值牌
- 中高强度反击牌
- 可防守牌
- 垃圾牌

这让它不会像很多 bot 一样只会二元决策。

### 18.2 翻后决策不是死阈值

虽然它是阈值系统，但阈值会受到：

- pot odds
- 对手紧松
- 场面压力
- 听牌状态

四类因素共同影响。

这比纯静态策略要强很多。

### 18.3 价值下注足够重

很多比赛 bot 的问题是：

- 会赢，但赢不大

而 `V2` 在自己明显领先时会更积极 build pot，所以更容易把优势转成真正的 EV。

### 18.4 听牌不会玩得太怂

它既不会：

- 什么听牌都乱冲

也不会：

- 听牌一被下注就弃

这个平衡对 heads-up 很重要。

---

## 19. 这份代码的局限

这部分你答辩时反而应该敢讲，因为讲得出局限，说明你真懂。

### 19.1 `estimate_pot` 是近似，不是真实底池

这会导致：

- 某些街的 pot odds 不够精确
- 某些 bet sizing 有偏差

但它胜在：

- 快
- 稳
- 足够好调

### 19.2 对手建模很粗

当前只有 `tight / loose` 两档。

它没法更精细地回答：

- 对手是不是喜欢 probe bet
- 对手是不是喜欢 limp
- 对手是不是 overbluff river

这也是我后来做 HUD 版 `V3` 的原因。

### 19.3 没有更细的 board texture 分析

比如：

- 干燥 A-high flop
- 双花连张湿润面
- paired board

这些局面在高级策略里会影响很大。  
`V2` 有隐性处理，但没有显式做更细分的牌面分类。

### 19.4 Monte Carlo 有随机噪声

这是所有小样本 equity 估计都会有的问题。

不过这份 bot 通过：

- 控制样本数
- 加安全边际
- 给听牌单独规则

把噪声风险压住了。

---

## 20. 如果你要在台上解释这份代码，可以怎么说

一句话版：

> AzaleaV2 is a fast exploitative bot that combines a lightweight preflop model, small-scale postflop equity estimation, and simple opponent profiling.

中文版：

> AzaleaV2 是一个偏 exploit 的快节奏 bot。翻前用启发式牌力模型，翻后用轻量 Monte Carlo 算胜率，再结合对手是偏紧还是偏松来动态调整跟注、再加注和价值下注阈值。

如果要再讲得像工程作品一点：

> 它不是靠重型搜索取胜，而是靠低延迟、稳定性和对常见 bot 风格的针对性来赢。平均决策时间远低于 1ms，本地测试大约在 0.1ms 量级。

---

## 21. 决策流程图（文字版）

你可以把 `move(...)` 理解为下面这张逻辑图：

### 翻前

1. 先算手牌强度
2. 看自己是否需要补注
3. 如果不用补注：
   - 强牌大开
   - 中牌标准开
   - 弱牌 check/fold
4. 如果要补注：
   - 顶级牌高压反击
   - 次强牌在合适尺寸下再加注
   - 中牌根据 pot odds 和对手类型防守
   - 太差则弃牌

### 翻后

1. 先估计 equity
2. 识别是否有强听牌
3. 统计场面是不是高压
4. 如果面对下注：
   - 强牌重做价值
   - 中强牌在低压时反打
   - 听牌和边缘牌按 pot odds 决定是否继续
5. 如果没人下注：
   - 强牌主动下注
   - 中牌继续争小池
   - 部分 flop 和 draw 场景主动 stab
   - 否则 check

---

## 22. 总结

`AzaleaV2` 的强，不是因为它看到了额外信息，也不是因为它做了超重的计算。  
它强在三点：

1. **翻前分层合理**
2. **翻后 equity + draw + pressure 三件事结合得比较好**
3. **整体风格够主动，但又没有主动到失控**

这也是为什么它能在当前测试池里稳定压过不少 bot。

如果后面继续进化，这份代码最自然的升级方向有两个：

1. 把粗糙的 `tight / loose` 画像升级成真正 HUD
2. 把 `estimate_pot` 从启发式改成更接近真实底池的状态跟踪

但就作为比赛提交版而言，`AzaleaV2` 已经是一份很像样、解释得通、也跑得快的 bot。

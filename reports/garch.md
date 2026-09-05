# GARCH / EGARCH 公式

## GARCH(1,1)

收益率方程：

$$
r_t = \mu + \varepsilon_t, \qquad \varepsilon_t = \sigma_t z_t, \qquad z_t \sim N(0,1)
$$

条件方差方程：

$$
\sigma_t^2 = \omega + \alpha\varepsilon_{t-1}^2 + \beta\sigma_{t-1}^2
$$

其中 $\varepsilon_{t-1}^2$ 是昨日冲击的大小，$\sigma_{t-1}^2$ 是昨日预测的波动率；通常要求 $\omega>0$、$\alpha,\beta\geq0$，并常见 $\alpha+\beta<1$。

### 昨日冲击怎样计算

冲击（残差）是实际收益减去模型在事前预测的收益：

$$
\varepsilon_{t-1} = r_{t-1} - \hat r_{t-1}
$$

在最简单的 GARCH 均值方程中，预测收益就是长期平均收益 $\mu$：

$$
\hat r_{t-1} = \mu
$$

因此：

$$
\varepsilon_{t-1} = r_{t-1} - \mu
$$

例如，若昨日实际日收益为 $-3\%$，模型预测为 $0.05\%$，则：

$$
\varepsilon_{t-1} = -3\% - 0.05\% = -3.05\%
$$

普通 GARCH 代入的是冲击平方：

$$
\varepsilon_{t-1}^2 = (-3.05\%)^2
$$

因此它不区分方向：意外上涨 3% 和意外下跌 3%，带来的方差更新相同。

## EGARCH(1,1)

$$
r_t = \mu + \varepsilon_t, \qquad \varepsilon_t = \sigma_t z_t
$$

$$
\log(\sigma_t^2)
=
\omega
+ \beta\log(\sigma_{t-1}^2)
+ \alpha\left(|z_{t-1}|-\mathbb E|z|\right)
+ \gamma z_{t-1}
$$

符号定义：

- $r_t$：第 $t$ 期实际收益率；$mu$：模型预测的平均收益率。
- $arepsilon_t$：第 $t$ 期收益中未被均值方程预测到的冲击（残差）。
- $sigma_t^2$：在 $t-1$ 期可得到的信息下，对第 $t$ 期收益方差的预测；$sigma_t$ 是相应条件标准差（条件波动率）。
- $z_t=\varepsilon_t/\sigma_t$：标准化冲击；在常见设定下 $z_t\sim N(0,1)$，即均值为 0、方差为 1。
- $omega$：对数条件方差的基础水平（截距）。
- $\beta$：波动率持久性；$\beta$ 越大，过去高波动或低波动状态延续得越久。
- $\alpha$：冲击绝对大小的影响；$|z_{t-1}|$ 越大，未来波动率通常越高。
- $\mathbb E|z|$：标准化冲击绝对值的理论均值；若 $z\sim N(0,1)$，则 $\mathbb E|z|=\sqrt{2/\pi}$。减去它后，“正常大小”的冲击对对数方差的平均增量为 0。
- $\gamma$：涨跌方向的不对称效应。股票中常见 $\gamma<0$：当 $z_{t-1}<0$（坏消息/意外下跌）时，$\gamma z_{t-1}>0$，会比同幅度正冲击更显著地抬高未来波动率。

取对数 $\log(\sigma_t^2)$ 的好处是：无论参数取值如何，指数变换后的 $\sigma_t^2$ 都必定为正。

EGARCH 使用标准化冲击：

$$
z_{t-1} = \frac{\varepsilon_{t-1}}{\sigma_{t-1}}
$$

它既衡量意外变动相对于当时正常波动有多大，也保留上涨或下跌的方向。

## GARCH-M（论文使用的思路）

“M” 代表 *in mean*：允许预期收益依赖条件波动率。

$$
r_t = \mu + \lambda\sigma_t^2 + \phi\varepsilon_{t-1} + \varepsilon_t
$$

再将上面的 $\sigma_t^2$ 代入 GARCH 或 EGARCH 方差方程。$\lambda$ 描述条件风险与预期收益的关系。

# 美国指数策略论文 PDF 索引

- 原始问题：

  > 论文pdf存到一个文件夹里给我，我看看

- 问题时间：2026-09-03 EDT（会话界面未提供原消息的秒级时间）
- 回答时间：2026-09-03 04:30:54 EDT（UTC-04:00，America/New_York）
- 获取原则：优先作者主页、NBER、央行、期刊和高校仓库公开版本；检索截止 2026-09-03。

## 1. 已确认事实

目录中有 19 份经 `pdfinfo` 验证的 PDF。文件名前两位沿用文献报告的顺序；缺少的 `08` 在下方单列。

| 文件 | 论文 | 本地版本及来源 |
|---|---|---|
| `01-bll-1992.pdf` | Brock, Lakonishok & LeBaron (1992) | 期刊版扫描件；[期刊记录](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x) |
| `02-stw-1999.pdf` | Sullivan, Timmermann & White (1999) | 期刊版；[期刊记录](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163) |
| `03-hsu-kuan-2005.pdf` | Hsu & Kuan (2005) | 作者稿；[作者 PDF](https://homepage.ntu.edu.tw/~ckuan/pdf/snoop01.pdf) |
| `04-zhu-zhou-2009.pdf` | Zhu & Zhou (2009) | 期刊版作者公开副本；[作者 PDF](https://guofuzhou.github.io/ZZ_JFE_09.pdf) |
| `05-mop-2012.pdf` | Moskowitz, Ooi & Pedersen (2012) | 期刊版作者公开副本；[作者 PDF](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf) |
| `06-hurst-2017.pdf` | Hurst, Ooi & Pedersen (2017) | AQR 公开期刊版；[AQR PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf) |
| `07-metghalchi-2016.pdf` | Metghalchi, Chen & Hajilee (2016) | 期刊开放版；[期刊 PDF](https://ojs.aut.ac.nz/applied-finance-letters/1/article/download/54/45) |
| `09-fleming-2001.pdf` | Fleming, Kirby & Ostdiek (2001) | 期刊版；[期刊记录](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00327) |
| `10-moreira-muir-2017.pdf` | Moreira & Muir (2017) | 2016 NBER working paper；[NBER PDF](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf) |
| `11-cederburg-2020.pdf` | Cederburg et al. (2020) | 期刊版作者公开副本；[作者 PDF](https://www.lehigh.edu/~xuy219/research/COWY.pdf) |
| `12-goyal-welch-2008.pdf` | Goyal & Welch (2008) | NBER working paper；[NBER PDF](https://www.nber.org/system/files/working_papers/w10483/w10483.pdf) |
| `13-neely-2014.pdf` | Neely et al. (2014) | St. Louis Fed working paper；[Fed PDF](https://files.stlouisfed.org/files/htdocs/wp/2010/2010-008.pdf) |
| `14-gwz-2024.pdf` | Goyal, Welch & Zafirov (2024) | 作者公开稿；[作者 PDF](https://www.ivo-welch.org/research/journalcopy/goyal2024comprehensive.pdf) |
| `15-gao-2018.pdf` | Gao et al. (2018) | 2014 working-paper 版本；[公开 PDF](https://smallake.kr/wp-content/uploads/2015/01/SSRN-id2440866.pdf)；[SSRN 记录](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866) |
| `16-baltussen-2021.pdf` | Baltussen et al. (2021) | 期刊版作者公开副本；[作者 PDF](https://academicweb.nd.edu/~zda/intramom.pdf) |
| `17-lakonishok-1988.pdf` | Lakonishok & Smidt (1988) | 多伦多大学课程页公开副本的 2024-09-06 Internet Archive 快照；[原公开 PDF](https://www-2.rotman.utoronto.ca/~kan/3032/pdf/AssetPricingAnomalies/Lakonishok_Smidt_RFS_1988.pdf) |
| `18-stw-2001.pdf` | Sullivan, Timmermann & White (2001) | 1998 LSE discussion paper；[LSE PDF](https://researchonline.lse.ac.uk/id/eprint/119142/1/dp304.pdf) |
| `19-coval-2001.pdf` | Coval & Shumway (2001) | 作者公开期刊版；[作者 PDF](https://www.tylergshumway.org/Coval-ExpectedOptionReturns-2001.pdf) |
| `20-btz-2009.pdf` | Bollerslev, Tauchen & Zhou (2009) | Federal Reserve working-paper 版本；[Fed 论文页](https://www.federalreserve.gov/pubs/feds/2007/200711/index.html) |

## 2. 确定性计算

- 目标论文：20 篇。
- 已取得并通过 PDF 结构验证：19 篇，`19 / 20 = 95%`。
- 文件总大小：23,116,495 bytes，即 22.05 MiB。
- 每个文件的 SHA-256 位于 `SHA256SUMS`；校验命令为 `sha256sum -c SHA256SUMS`。

## 3. 解释、反证与不确定性

- 缺少 `08-ren-2023.pdf`。期刊官方页面显示可下载，但当前下载端点被出版平台的 Cloudflare/AWS WAF 拒绝，返回 HTML 挑战而不是 PDF；为避免把错误页伪装成论文，目录中只保存了 `08-ren-2023.url` 官方链接。该论文只有第 32–33 页两页。[官方页面](https://digitalcommons.wcupa.edu/pennsylvania-economic-review/vol30/iss2/3/)
- `10`、`12`、`13`、`15`、`18`、`20` 是 working-paper/discussion-paper 版本，内容可能和最终排版版有少量差异；索引已经明确标注，没有冒充最终出版社 PDF。
- `17` 的公开源目前存在 TLS 兼容问题，文件取自同一公开 URL 的 Internet Archive 固定快照；它是图像扫描 PDF，文本搜索效果较差。
- 本目录用于个人研究阅读。不同 PDF 的版权与再分发条件各自独立，不应把整个目录作为公开数据包再次发布。

## 4. 单一下一实验或 Proposal

唯一后续动作是：从不受当前 WAF 限制的浏览器网络打开 `08-ren-2023.url`，下载后用 `pdfinfo` 验证为两页 PDF，并核对标题和 DOI `10.65193/3067-8080.1031`。只有三项都匹配，才保存为 `08-ren-2023.pdf` 并补写 SHA-256；否则维持当前 19/20 状态。

"""Build the deterministic, offline Markdown interoperability test documents."""
from pathlib import Path
import json
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1] / 'tests/fixtures/markdown_compatibility'

BASIC = r'''---
title: Markdown、數學與物理相容性測試
aliases:
  - MD 格式測試
tags:
  - compatibility
  - math
test_version: 1
---

# Markdown、數學與物理相容性測試

這是一份可重複使用的測試文件。請將整個資料夾一起保留，以便測試相對連結、圖片及筆記嵌入。

用途是核對常見 Markdown、Obsidian 核心語法與 LLM 輸出的數學／物理文字；不是承諾所有 LaTeX 套件或 Obsidian 外掛均可執行。一般公式使用 `$…$`、`$$…$$`；後面的反斜線分隔符區另外檢查 LLM 常見的 `\(…\)`、`\[…\]`。

## 驗收順序

- [ ] 閱讀：文字、圖片、表格、公式沒有遺漏或重複。
- [ ] 編寫：進入內容後仍可修改原始語法。
- [ ] 保存：存檔、關閉再重開後，公式與連結一致。
- [ ] 復原：改一個數字後 Undo／Redo，數字能正確來回切換。
- [ ] 可攜：把整個資料夾複製到另一位置，圖片、相對連結、嵌入仍可用。
- [ ] 比對：在 PCS 與 Obsidian 分別閱讀；記錄差異，不能以原文還在就當作顯示成功。

## 01 標題、段落與文字樣式

### 三級標題
#### 四級標題
##### 五級標題
###### 六級標題

一般文字、**粗體**、*斜體*、***粗斜體***、~~刪除線~~、==螢光標示==、`行內程式碼`。

這是一個普通段落。中文與 English、數字 0123456789、標點「」『』（）【】都應保留。

這一段的兩行以普通換行相接，
是否合併受嚴格換行設定影響。

第一行硬換行。  
第二行應另起一行。

HTML 換行前<br>HTML 換行後。

---

## 02 清單、引用、提示與註解

- 第一層項目
  - 第二層項目
    - 第三層項目
- 第二個第一層項目

3. 從第三項開始。
4. 第四項包含 **粗體** 與 $x^2$。

> 引用第一段。
>
> 引用第二段，含 $E=mc^2$。
>
> > 巢狀引用。

> [!note] 一般提示
> 內容應在提示區，並保留 **粗體**。

> [!tip]+ 預設展開
> 展開後可見這一行。

> [!warning]- 預設收合
> 點擊標題後才看見這一行。

可見文字。%% 這段 Obsidian 註解只應在原文中出現。 %%接續可見文字。

<!-- HTML 註解也不應出現在閱讀畫面。 -->

## 03 連結、圖片、腳註與嵌入

[相對連結](輔助筆記.md)、[[輔助筆記]]、[[輔助筆記|顯示別名]]。

[[輔助筆記#連結目標]]、[[輔助筆記#^test-block]]。

[Obsidian 語法說明](https://help.obsidian.md/obsidian-flavored-markdown)、[參照連結][docs]。

[docs]: https://help.obsidian.md/advanced-syntax "進階格式"

這裡有普通腳註[^normal]，以及第二個腳註[^second]。

[^normal]: 普通腳註應可跳轉與返回，並保留 **粗體** 和 $x_i$。
[^second]: 第二個腳註含 [來源連結](https://example.com/)。

下圖應是左紅、右藍的 96×48 測試圖片，不應出現破圖：

![紅藍測試圖](assets/red-blue.png)

指定尺寸的圖片：![紅藍測試圖|48x24](assets/red-blue.png)

Obsidian 圖片嵌入：![[assets/red-blue.png|48]]

嵌入完整筆記：

![[輔助筆記]]

只嵌入指定區塊：

![[輔助筆記#^test-block]]

## 04 表格與精確數值

| 項目 | 內容 | 數值 |
| :--- | :---: | ---: |
| **粗體** | 普通文字 | 001.2300 |
| 根號 | $\sqrt{2}$ | 1.4142135623730951 |
| 科學記號 | $6.02214076\times10^{23}$ | 6.02214076e23 |
| 小數 | $1.0000000000000001$ | 1.0000000000000001 |
| 豎線 | `a\|b` | 9007199254740993 |
| 連結別名 | [[輔助筆記\|別名]] | -0.0000000000000001 |
| 圖片 | ![測試圖\|48](assets/red-blue.png) | 299792458 |

預期：欄數不變；前置零、尾隨零、正負號與所有數字逐字保留。不要經浮點數轉換、四捨五入或加千分位。

<table>
<caption>合併儲存格</caption>
<thead><tr><th>類別</th><th>甲</th><th>乙</th></tr></thead>
<tbody><tr><th rowspan="2">同一類</th><td>1.00</td><td>2.00</td></tr><tr><td colspan="2">橫跨兩欄</td></tr></tbody>
</table>

## 05 程式碼、跳脫與字元

這些符號是字面文字，不應變成排版：\*星號\*、\_底線\_、[方括號]、\#井字、\$美元、反斜線 \\。

LLM 常用的公式分隔符若要顯示為字面文字，應放在程式碼中：`\[方括號\]`、`\(圓括號\)`。

行內原文：`\(x^2\)`、`$E=mc^2$`、`\frac{a}{b}`、`[[這不是連結]]`。

```python
sample = r"\[\int_0^1 x^2\,dx\]"
path = r"C:\notes\math.md"
literal = "**not bold** | $100 | [[not a link]]"
```

```text
\(x^2\)
\[\sum_{i=1}^{n}i\]
$$\sqrt{2}$$
%% literal comment %%
```

普通數值：µ μ Ω ω ° Å ± × ÷ ∂ ∇ ∫ ∑ √ ∞ ≈ ≠ ≤ ≥ → ↔ ℏ。

直接文字的上標／下標：m²、m³、s⁻¹、CO₂、H₂O；HTML 的上標／下標：m<sup>2</sup>、x<sub>i</sub>。

貨幣控制：$5 和 $10；US$20.00；跳脫的 \$5。這些不應被當成一段公式。

## 06 圖解

```mermaid
flowchart LR
  A[原始 Markdown] --> B[閱讀顯示]
  A --> C[編輯與保存]
  C --> D[重新開啟]
```

預期：有向圖包含四個節點；圖形只是原文的顯示，不能覆蓋原始 Mermaid 程式碼。

'''

GROUPS = [
('07 基本數學、字母與界符', [
('加減乘除與正負', r'a+b-c\times d\div e\pm f\mp g'),
('分數與連分數', r'\frac{a+b}{c-d}+\dfrac{1}{1+\cfrac{1}{2+\cfrac{1}{3}}}'),
('平方根與 n 次根', r'\sqrt{x^2+y^2},\qquad\sqrt[3]{8}=2,\qquad\sqrt[n]{a}'),
('上下標與多層索引', r'x_i^2,\quad a_{ij},\quad e^{-x^2},\quad T^{\mu}_{\ \nu}'),
('希臘字母', r'\alpha\beta\gamma\delta\epsilon\varepsilon\theta\vartheta\lambda\mu\nu\xi\pi\rho\sigma\tau\phi\varphi\chi\psi\omega'),
('大寫希臘字母', r'\Gamma\Delta\Theta\Lambda\Xi\Pi\Sigma\Upsilon\Phi\Psi\Omega'),
('集合與邏輯', r'\forall x\in\mathbb{R},\quad \exists y\in\mathbb{C}:\quad A\subseteq B,\ A\cup B,\ A\cap B,\ \varnothing'),
('不等式與極限關係', r'a\ne b,\quad a\le b,\quad a\ge b,\quad a\approx b,\quad a\equiv b\pmod n,\quad a\propto b'),
('自動伸縮括號', r'\left(\frac{a}{b}\right),\quad\left[\sum_{i=1}^n i\right],\quad\left\langle\psi\middle|\phi\right\rangle'),
('絕對值與範數', r'|x|,\quad\lvert x\rvert,\quad\lVert\mathbf{v}\rVert_2,\quad\lfloor x\rfloor,\quad\lceil x\rceil'),
('三角、指數與對數', r'\sin^2\theta+\cos^2\theta=1,\quad e^{i\pi}+1=0,\quad\ln x,\quad\log_{10}x,\quad\exp(x)'),
('複數與共軛', r'z=a+bi,\quad\overline{z}=a-bi,\quad\operatorname{Re}z=a,\quad\operatorname{Im}z=b'),
]),
('08 微分、積分、級數', [
('普通微分', r'\frac{\mathrm{d}y}{\mathrm{d}x}=\lim_{h\to0}\frac{f(x+h)-f(x)}{h}'),
('高階導數與撇號', r'f^{\prime}(x),\quad f^{\prime\prime}(x),\quad\frac{\mathrm{d}^n f}{\mathrm{d}x^n}'),
('時間微分', r'\dot{x}=\frac{\mathrm{d}x}{\mathrm{d}t},\quad\ddot{x}=\frac{\mathrm{d}^2x}{\mathrm{d}t^2}'),
('偏微分與混合偏導', r'\frac{\partial f}{\partial x},\quad\frac{\partial^2 f}{\partial x\partial y},\quad\partial_\mu\phi'),
('全微分', r'\mathrm{d}f=\frac{\partial f}{\partial x}\,\mathrm{d}x+\frac{\partial f}{\partial y}\,\mathrm{d}y'),
('不定積分', r'\int x^n\,\mathrm{d}x=\frac{x^{n+1}}{n+1}+C\quad(n\ne-1)'),
('定積分', r'\int_0^1 x^2\,\mathrm{d}x=\frac{1}{3}'),
('反常積分', r'\int_{-\infty}^{\infty}e^{-x^2}\,\mathrm{d}x=\sqrt{\pi}'),
('二重與三重積分', r'\iint_D f(x,y)\,\mathrm{d}A,\qquad\iiint_V\rho\,\mathrm{d}V'),
('線積分與閉合積分', r'\int_C\mathbf{F}\cdot\mathrm{d}\mathbf{r},\qquad\oint_C\mathbf{F}\cdot\mathrm{d}\mathbf{r}'),
('求和 Sigma', r'\sum_{i=1}^{n}i=\frac{n(n+1)}{2}'),
('連乘 Pi', r'\prod_{k=1}^{n}k=n!'),
('泰勒級數', r'e^x=\sum_{n=0}^{\infty}\frac{x^n}{n!}'),
('極限', r'\lim_{x\to0}\frac{\sin x}{x}=1,\qquad\lim_{n\to\infty}\left(1+\frac1n\right)^n=e'),
('鏈鎖法則', r'\frac{\mathrm{d}}{\mathrm{d}x}f(g(x))=f^{\prime}(g(x))\,g^{\prime}(x)'),
]),
('09 向量、矩陣與線性代數', [
('向量與單位向量', r'\vec{v}=v_x\hat{\mathbf{x}}+v_y\hat{\mathbf{y}}+v_z\hat{\mathbf{z}}'),
('點積與叉積', r'\mathbf{a}\cdot\mathbf{b}=ab\cos\theta,\quad\mathbf{a}\times\mathbf{b}=ab\sin\theta\,\hat{\mathbf{n}}'),
('基底旋轉', r'\mathbf{e}_x\longrightarrow(\cos\theta,\sin\theta),\quad\mathbf{e}_y\longrightarrow(-\sin\theta,\cos\theta)'),
('二維旋轉矩陣', r'R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix}'),
('三維矩陣', r'A=\begin{bmatrix}a_{11}&a_{12}&a_{13}\\a_{21}&a_{22}&a_{23}\\a_{31}&a_{32}&a_{33}\end{bmatrix}'),
('行列式', r'\begin{vmatrix}a&b\\c&d\end{vmatrix}=ad-bc'),
('轉置、逆矩陣與厄米共軛', r'A^{\mathsf{T}},\quad A^{-1},\quad A^{\dagger}=(\overline{A})^{\mathsf{T}}'),
('特徵值', r'A\mathbf{v}=\lambda\mathbf{v},\qquad\det(A-\lambda I)=0'),
('分段函數', r'f(x)=\begin{cases}x^2,&x\ge0,\\-x,&x<0.\end{cases}'),
('對齊多行公式', r'\begin{aligned}a&=b+c\\&=d+e\\f(x)&=x^2+1\end{aligned}'),
('聯立方程', r'\left\{\begin{aligned}2x+y&=5\\x-y&=1\end{aligned}\right.'),
('Jacobian', r'J=\frac{\partial(u,v)}{\partial(x,y)}=\begin{pmatrix}\frac{\partial u}{\partial x}&\frac{\partial u}{\partial y}\\\frac{\partial v}{\partial x}&\frac{\partial v}{\partial y}\end{pmatrix}'),
]),
('10 物理與向量微積分', [
('梯度', r'\nabla f=\left(\frac{\partial f}{\partial x},\frac{\partial f}{\partial y},\frac{\partial f}{\partial z}\right)'),
('散度', r'\nabla\cdot\mathbf{F}=\frac{\partial F_x}{\partial x}+\frac{\partial F_y}{\partial y}+\frac{\partial F_z}{\partial z}'),
('旋度', r'\nabla\times\mathbf{F}=\begin{vmatrix}\hat{\mathbf{x}}&\hat{\mathbf{y}}&\hat{\mathbf{z}}\\\partial_x&\partial_y&\partial_z\\F_x&F_y&F_z\end{vmatrix}'),
('Laplacian', r'\nabla^2\phi=\frac{\partial^2\phi}{\partial x^2}+\frac{\partial^2\phi}{\partial y^2}+\frac{\partial^2\phi}{\partial z^2}'),
('散度定理', r'\iiint_V\nabla\cdot\mathbf{F}\,\mathrm{d}V=\iint_{\partial V}\mathbf{F}\cdot\hat{\mathbf{n}}\,\mathrm{d}S'),
('Stokes 定理', r'\iint_S(\nabla\times\mathbf{F})\cdot\hat{\mathbf{n}}\,\mathrm{d}S=\oint_{\partial S}\mathbf{F}\cdot\mathrm{d}\mathbf{r}'),
('牛頓運動與動能', r'\mathbf{F}=m\mathbf{a},\quad\mathbf{p}=m\mathbf{v},\quad K=\frac12mv^2'),
('振動與阻尼', r'm\ddot{x}+b\dot{x}+kx=F_0\cos(\omega t)'),
('Euler–Lagrange 方程', r'\frac{\mathrm{d}}{\mathrm{d}t}\frac{\partial L}{\partial\dot{q}_i}-\frac{\partial L}{\partial q_i}=0'),
('Hamilton 方程', r'\dot{q}_i=\frac{\partial H}{\partial p_i},\qquad\dot{p}_i=-\frac{\partial H}{\partial q_i}'),
('Maxwell 方程', r'\begin{aligned}\nabla\cdot\mathbf{E}&=\rho/\varepsilon_0\\\nabla\cdot\mathbf{B}&=0\\\nabla\times\mathbf{E}&=-\partial_t\mathbf{B}\\\nabla\times\mathbf{B}&=\mu_0\mathbf{J}+\mu_0\varepsilon_0\partial_t\mathbf{E}\end{aligned}'),
('波動方程', r'\frac{\partial^2u}{\partial t^2}=c^2\nabla^2u'),
('Schrödinger 方程', r'i\hbar\frac{\partial\psi}{\partial t}=\left(-\frac{\hbar^2}{2m}\nabla^2+V\right)\psi'),
('量子態與對易子', r'\langle\psi|\hat{A}|\psi\rangle,\quad[\hat{x},\hat{p}]=i\hbar,\quad\Delta x\,\Delta p\ge\frac{\hbar}{2}'),
('相對論', r'E^2=p^2c^2+m^2c^4,\qquad\gamma=\frac{1}{\sqrt{1-v^2/c^2}}'),
('熱力學', r'\mathrm{d}U=T\,\mathrm{d}S-p\,\mathrm{d}V+\mu\,\mathrm{d}N'),
('Gibbs 熵', r'S=-k_{\mathrm{B}}\sum_i p_i\ln p_i'),
('Poisson 括號', r'\{f,g\}=\sum_i\left(\frac{\partial f}{\partial q_i}\frac{\partial g}{\partial p_i}-\frac{\partial f}{\partial p_i}\frac{\partial g}{\partial q_i}\right)'),
]),
('11 機率、統計、工程與單位', [
('機率與條件機率', r'P(A\mid B)=\frac{P(A\cap B)}{P(B)}'),
('平均、變異與標準差', r'\bar{x}=\frac1n\sum_{i=1}^n x_i,\quad\sigma^2=\mathbb{E}[(X-\mu)^2],\quad\sigma=\sqrt{\sigma^2}'),
('常態分布', r'f(x)=\frac{1}{\sigma\sqrt{2\pi}}\exp\left(-\frac{(x-\mu)^2}{2\sigma^2}\right)'),
('二項係數', r'\binom{n}{k}=\frac{n!}{k!(n-k)!}'),
('Fourier 轉換', r'\hat{f}(\omega)=\int_{-\infty}^{\infty}f(t)e^{-i\omega t}\,\mathrm{d}t'),
('Laplace 轉換', r'F(s)=\int_0^\infty f(t)e^{-st}\,\mathrm{d}t'),
('電路阻抗', r'Z_R=R,\quad Z_L=i\omega L,\quad Z_C=\frac{1}{i\omega C}'),
('微小量與有效位數', r'1.2300\times10^{-9}\,\mathrm{m},\qquad 6.02214076\times10^{23}\,\mathrm{mol}^{-1}'),
('工程單位', r'9.80665\,\mathrm{m\,s^{-2}},\quad 1.00\,\mathrm{k\Omega},\quad 2.20\,\mu\mathrm{F},\quad 25.0\,{}^{\circ}\mathrm{C}'),
('不確定度', r'L=(12.340\pm0.005)\,\mathrm{mm},\qquad \frac{\Delta L}{L}=4.05\times10^{-4}'),
('量綱', r'[F]=\mathsf{M}\mathsf{L}\mathsf{T}^{-2},\qquad [E]=\mathsf{M}\mathsf{L}^{2}\mathsf{T}^{-2}'),
('百分比、符號與中文', r'\eta=98.50\%,\qquad\text{誤差}\le0.01\,\mathrm{mm}'),
]),
]

TAIL = r'''
## 12 行內公式與不同分隔符

行內公式要與文字同一行：$x_i^2$、$\frac{1}{2}$、$\sqrt{3}$、$\partial_t\psi$、$\nabla\cdot\mathbf{F}$、$\Sigma$、$\sum_{i=1}^n i$。

LLM 常見行內分隔符：\(R(90^\circ)\)、\(\mathbf{e}_x\)、\(\int_0^1x^2\,\mathrm{d}x\)。

LLM 常見單行顯示公式：

\[ R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix} \]

LLM 常見多行顯示公式：

\[
\begin{aligned}
\mathbf{v}&=x\mathbf{e}_x+y\mathbf{e}_y\\
T(\mathbf{v})&=xT(\mathbf{e}_x)+yT(\mathbf{e}_y)
\end{aligned}
\]

1. 清單內行內公式 \(x^2+y^2=r^2\)。
2. 清單內顯示公式：

   \[
   \sum_{i=1}^{n}i=\frac{n(n+1)}2
   \]

> 引用內顯示公式：
>
> \[
> E=mc^2
> \]

## 13 異常與不支援語法的控制組

這一節刻意保留原文。不要自動刪除它們，也不要猜測其數學意思。

未閉合的行內公式：\(x+1

未閉合的美元公式：$x+1

未閉合的顯示公式：

\[
x+1

以下是套件或自訂指令，僅作字面資料保留；這份測試不要求把它們冒充已成功排版：

```latex
\usepackage{physics}
\dv{x}{t}
\pdv{f}{x}
\qty(\frac{a}{b})
\SI{9.81}{\metre\per\second\squared}
\ce{H2O}
\begin{tikzpicture} ... \end{tikzpicture}
```

若需要這類功能，應另外選定套件相容範圍。GeoGebra 類互動數學工具也屬另一項功能，不把靜態公式等同互動模型。

## 14 保存、還原與原文比對

修改用數值：`001.2300`、`9007199254740993`、`-0.0000000000000001`。

修改用公式：$a_{12}=1.0000000000000001$。

先記下原文，再修改其中一個數字、保存、關閉、重新開啟，核對沒有轉換數字或吞掉反斜線。接著測試 Undo／Redo；這應恢復本次編輯，不能誤改別的檔案。

## 參考與邊界

- [Obsidian 核心 Markdown 語法](https://help.obsidian.md/obsidian-flavored-markdown)
- [Obsidian 進階格式與 MathJax](https://help.obsidian.md/advanced-syntax)
- [PCS 目前採用的 KaTeX 支援列表](https://katex.org/docs/supported.html)

Obsidian 使用 MathJax，PCS 此版本使用本機 KaTeX。兩者共有大量數學語法，但套件、巨集及邊界行為並不完全一致。此文件涵蓋常見格式與代表性邊界，不能窮舉任意使用者自訂的數學指令。
'''


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    row = b'\x00' + bytes((220, 65, 65)) * 48 + bytes((55, 120, 220)) * 48
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 96, 48, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * 48)) + chunk(b'IEND', b''))


def build(root=ROOT):
    root = Path(root)
    (root / 'assets').mkdir(parents=True, exist_ok=True)
    parts = [BASIC]
    formulas = []
    for group, cases in GROUPS:
        parts.append('## ' + group + '\n')
        for label, tex in cases:
            ident = f'M{len(formulas) + 1:03}'
            formulas.append(dict(id=ident, label=label, tex=tex))
            parts.append(f'### {ident} {label}\n\n$$\n{tex}\n$$\n\n原文：`{tex}`\n')
    parts.append(TAIL)
    (root / 'Markdown-相容性測試.md').write_text('\n'.join(parts), encoding='utf-8')
    (root / '輔助筆記.md').write_text('# 輔助筆記\n\n## 連結目標\n\n相對連結與標題連結都應能找到這裡。\n\n這是指定區塊；包含公式 $E=mc^2$。 ^test-block\n', encoding='utf-8')
    (root / 'assets/red-blue.png').write_bytes(png())
    (root / 'formula-cases.json').write_text(json.dumps(formulas, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return root


if __name__ == '__main__':
    print(build())

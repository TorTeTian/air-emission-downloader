# Air Emission Downloader

一个skill，自动按用户指令选择地区或企业模式，获取中国生态环境部公开排污许可大气执行报告。

|用户要求|选择|
|---|---|
|常熟市所有持证企业的2023、2024年JJA记录|按地区：官方地市目录 → 生产经营场所县区核验|
|江苏常熟发电有限公司的2023、2024年JJA记录|按企业：名称/许可证检索 → 精确身份核验|

## 安装与调用

将`skills/air-emission-downloader`复制到你的skill目录，例如Codex的`~/.codex/skills/air-emission-downloader`，新会话调用`$air-emission-downloader`。其他harness也可显式读取该文件夹的`SKILL.md`。

```text
使用 $air-emission-downloader，获取江苏省苏州市常熟市2023、2024年JJA大气排污记录。
先做首页前6个企业详情的小样本，最多40次请求，输出到E:/permit_air/changshu_pilot。
不启动全量，核验来源、单位、缺失和覆盖范围。
```

```text
使用 $air-emission-downloader，获取江苏常熟发电有限公司2023、2024年JJA大气排污记录。
输出到E:/permit_air/changshu_power，最多40次请求。核对许可证与场址，
保留全部大气指标以及全厂/排口口径，完成后运行来源回读核验。
```

## 环境

Python 3.10+；在skill目录运行：

```shell
python -m pip install -r scripts/requirements.txt
```

harness需要Python/shell执行、文件读写、HTTPS GET/POST、长任务轮询。无需GPU、多模态、API密钥或企业账号。脚本处理分页、下载和算术，模型负责范围、身份消歧和异常。未对特定廉价模型的成本/成功率做基准测试。

## 验证

```shell
python -B -X utf8 -m unittest discover -s skills/air-emission-downloader/scripts -p "test_*.py" -v
```

测试不联网。可选的历史缓存回读测试通过环境变量`MEE_AIR_TEST_MANIFEST`指定已有缓存manifest；未提供时跳过这一项，其余测试自包含。

合并前两入口已完成2个真实案例的独立下载复跑：8份大气JSON正文、283条逐月字段一致。合并仅改变入口和文档路由，下载内核及数据规则不变。完整原始企业数据、HTTP缓存和本地报告不放入此仓库。

## 口径与边界

- 只适配JJA（6—8月）；月报或季报中的实际月份字段，不把季度/年度总量均分。
- 保留污染物原代码、单位、来源、原始字段、缺失和冲突；全厂与排口分开，不重复求和。
- 下载结果是官方匿名查看器的大气JSON投影，不是原始PDF；旧版PDF未自动解析。
- 当前公开许可目录不等于所有历史曾持证企业；注销/撤销历史全集未覆盖。仅登记管理企业不算许可企业。
- 默认串行限速；超过100次请求或1万条记录的计划先获用户批准；遇访问限制或结构变化停止，不绕过。

仓库保存可复用技能代码、协议及测试，不包含账号凭据或研究数据集。

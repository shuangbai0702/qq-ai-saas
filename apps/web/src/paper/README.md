# 已确认的 03 纸面元素

原件：`public/design-lab/20261101/03-cotton/`。第八轮原件基线与两份归档已保留；第九轮样板经用户确认后继续接入。

- `paper-renderer.js`、`dry-ink.js`、`pigment-feedback.js` 直接复制原实现。
- `wallpaper-paint.js` 仅调整默认素材路径至 `/paper/deepseek-desk.png`；圆形干笔、时间分组、停留和消退参数沿用原件。
- `paper-surface.js` 从原 `study.js` 提取表面类和几何算法，去掉移动子节点的按钮文字包装，补充真实页面隐藏/零尺寸元素的初始化保护。
- `runtime.js` 为真实 DOM 附加原 SVG / canvas 表面；不替换控件、不合成业务点击、不修改表单值。原生开关的纸层位于输入下方；文件选择按钮仅在原生表面上叠加不接收事件的正片叠底染印。返回、引导快捷入口与固定动态按钮也接入同一效果。React 增删页面、重写选中类名或创建 portal 弹窗后恢复自有纸层；退出释放自有资源。
- `paper.css` 负责实际页面布局和旧主题覆盖。原 `style.css` 保留。
- `paper-ink.js` 直接迁移第九轮的浅拓印、纤维线和留白套印选区。表格文字不做矩形挖空；文字、输入值、原生选区与复制保持真实语义。
- `paper-details.js` 将这些画笔映射到实际表格、输入框、原生折叠栏及内存折线图。各序列保留原配色，统一使用原红色线的干墨工艺与节点，取消线下区域填充；悬停跟随鼠标附近的序列及其配色。图表读取现有 SVG 的真实路径和投影矩阵，保留未知值断点、轴标与说明，不添加样板数据。表格按活动行分配纹理，保留真实滚动口的小数尺寸并裁切，避免取整后的装饰画布超出容器；输入与选择图层随原生弹窗进入顶层，关闭后移回。

单个运行实例拥有共享材质渲染器、时钟、观察器和监听器；无活动笔触/反馈时停止 RAF。减少动态、手动暂停、页面隐藏与历史缓存会冻结动态时间。WebGL 不可用或上下文丢失时使用原 Canvas2D 回退。

2026-11-03 性能调整：壁纸停留阶段没有新笔触时复用已绘画面，淡出阶段只合成当前与上一帧笔触覆盖的区域；采样坐标、像素密度、笔刷与生命周期保持原值。纸面 SVG 的相同属性不重复提交，空干墨层不重复清空。未新增定时器、观察器或独立动画循环。实测记录见 `docs/paper-background-performance-20261103.md`。

本地验证（复用 5173；真实 API 测试数据自动隔离，不启动 Agent）：

```text
node --import tsx tests/paper-browser.ts
node --import tsx tests/paper-effects-browser.ts
node --import tsx tests/paper-details-browser.ts
node --import tsx tests/paper-layout-browser.ts
```

前两项使用隔离真实 API 或实际登录页；第三项为真实 React 内存页配脱敏 UI 采样夹具，并补充动态原生弹窗；第四项使用原生滚动条，核对用户与名额页底部、自有画布裁切，以及列表 / 侧栏 / QQ 页连接文字是否位于真实纸面轮廓内。后两项禁止业务写入。第九轮计划与验收见 `public/design-lab/20261101/REFINEMENT-R9.md`；后续纠正与当前结果见 `docs/paper-console-integration-20261102.md`。

素材来源与参考许可保留于 `public/paper/`。

"""真实世界系统的接入适配器（SaleSmartly / 支付 / 供应商 …）。

每个模块把一个外部系统「归一化」成 company.record + roles 能吃的形状。
入站已按官方文档做真实现的：salesmartly（webhook 前门）。
"""

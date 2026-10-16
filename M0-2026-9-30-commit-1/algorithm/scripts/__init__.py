"""拾光算法服务的开发辅助脚本（数据生成、校验）。

这些脚本不参与线上运行，仅用于本地开发与评测数据准备：
- ``generate_demo_data.py``   确定性生成 data/demo_items.json 与 data/demo_matches.json
- ``verify_demo_data.py``     校验演示数据集的规模、字段、标签与分值区间
- ``verify_yaml_parser.py``   校验内置极简 YAML 解析器与 PyYAML 结果一致
"""

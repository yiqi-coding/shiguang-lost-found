"""演示数据构造（确定性、无随机、全部人工合成）。

本模块提供两类能力：

1. :func:`build_demo_items` / :func:`build_demo_matches`
   —— 生成 ``data/demo_items.json`` 与 ``data/demo_matches.json`` 的内容，
      完全由固定模板 + 索引算术决定，**不调用 random，反复生成结果一致**。
2. 运行期辅助：:func:`get_demo_query` 等，方便本地联调接口。

生成脚本：``python scripts/generate_demo_data.py``
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .locations import get_index

MODEL_VERSION = "baseline-1.0"

#: 演示用的固定基准时间（全部数据都由它偏移得到，保证可重复）
BASE_TIME = "2026-09-28T15:00:00"

CATEGORIES: Tuple[str, ...] = ("无线耳机", "水杯", "书籍", "校园卡", "雨伞", "背包")

#: 每类 5 个变体（同一变体 = 同一件物品的外观/隐藏特征，用于构造正样本对）
VARIANTS: Dict[str, List[Dict[str, Any]]] = {
    "无线耳机": [
        {"color": "黑色", "material": "塑料", "brand": "Apple", "model": "AirPods Pro 2",
         "public_features": ["椭圆形充电盒", "盒体右侧有划痕"],
         "hidden_features": ["盒盖内侧有一道3厘米划痕", "蓝牙名称 YiQi-AirPods"],
         "keywords": ["蓝牙耳机", "黑色", "无线"], "accessories": ["左右耳机", "充电盒"]},
        {"color": "白色", "material": "塑料", "brand": "华为", "model": "FreeBuds 5i",
         "public_features": ["鹅卵石形充电盒", "左耳柄有磨损"],
         "hidden_features": ["左耳塞网罩有一处凹陷", "蓝牙名称 HUAWEI-FreeBuds-5i-8821"],
         "keywords": ["蓝牙耳机", "白色", "降噪"], "accessories": ["左右耳机", "充电盒"]},
        {"color": "银色", "material": "金属", "brand": "索尼", "model": "WH-1000XM4",
         "public_features": ["头戴式", "耳罩有磨损"],
         "hidden_features": ["头梁内侧贴有一张褪色贴纸", "蓝牙名称 WH-1000XM4-SZ"],
         "keywords": ["头戴耳机", "银色", "降噪"], "accessories": ["耳机线", "收纳袋"]},
        {"color": "蓝色", "material": "塑料", "brand": "漫步者", "model": "LolliPods Plus",
         "public_features": ["方形充电盒", "盒角有磕碰"],
         "hidden_features": ["盒底刻有一位舍友的手机号尾号", "蓝牙名称 EDIFIER-LP-0527"],
         "keywords": ["蓝牙耳机", "蓝色", "半入耳"], "accessories": ["左右耳机", "数据线"]},
        {"color": "黑色", "material": "硅胶", "brand": "小米", "model": "Redmi Buds 4",
         "public_features": ["圆柱形充电盒", "包装贴纸残留"],
         "hidden_features": ["充电盒内壁有茶色污渍", "蓝牙名称 RedmiBuds4-3390"],
         "keywords": ["蓝牙耳机", "黑色", "入门"], "accessories": ["左右耳机"]},
    ],
    "水杯": [
        {"color": "白色", "material": "不锈钢", "brand": "膳魔师", "model": "JNL-402",
         "public_features": ["杯身有贴纸", "杯盖有划痕"],
         "hidden_features": ["杯底刻有「TY」两个字母", "杯盖内侧有一圈茶渍"],
         "keywords": ["保温杯", "白色", "便携"], "accessories": ["杯盖", "吸管"]},
        {"color": "蓝色", "material": "塑料", "brand": "乐扣乐扣", "model": "ABF-680",
         "public_features": ["带吸管", "杯身有卡通图案"],
         "hidden_features": ["吸管底部有一个小缺口", "杯身贴纸背面写了宿舍号"],
         "keywords": ["水杯", "蓝色", "吸管杯"], "accessories": ["杯盖", "吸管"]},
        {"color": "绿色", "material": "玻璃", "brand": "富光", "model": "FG-1200",
         "public_features": ["透明玻璃杯", "杯口有磨损"],
         "hidden_features": ["杯壁内侧有一道细裂纹", "杯垫上写了姓名缩写"],
         "keywords": ["玻璃杯", "绿色", "办公"], "accessories": ["杯盖"]},
        {"color": "黑色", "material": "不锈钢", "brand": "象印", "model": "SM-SA48",
         "public_features": ["咖啡色杯身贴纸", "杯底有划痕"],
         "hidden_features": ["杯底标签写着房间号 512", "杯盖内圈有一道浅划痕"],
         "keywords": ["保温杯", "黑色", "咖啡"], "accessories": ["杯盖"]},
        {"color": "粉色", "material": "陶瓷", "brand": "网易严选", "model": "YH-MUG-01",
         "public_features": ["马克杯", "手柄有缺口"],
         "hidden_features": ["杯底有手写「2023 秋」字样", "杯口内侧有一处小崩瓷"],
         "keywords": ["马克杯", "粉色", "陶瓷"], "accessories": ["杯盖", "小勺"]},
    ],
    "书籍": [
        {"color": "绿色", "material": "纸", "brand": None, "model": None,
         "public_features": ["书脊有标签", "封面有折痕"],
         "hidden_features": ["第 42 页夹有一张公交卡大小的书签", "扉页写着「李某某 2023」"],
         "keywords": ["教材", "高等数学", "绿色"], "accessories": ["笔记本"]},
        {"color": "蓝色", "material": "纸", "brand": None, "model": None,
         "public_features": ["书角磨损", "封面有胶带"],
         "hidden_features": ["书脊内侧写有学号后四位 0421", "第 87 页有荧光笔标记"],
         "keywords": ["教材", "线性代数", "蓝色"], "accessories": ["笔记本", "笔"]},
        {"color": "白色", "material": "纸", "brand": "得力", "model": None,
         "public_features": ["白色封面", "贴有姓名贴"],
         "hidden_features": ["最后一页写有两行公式草稿", "扉页盖有社团印章"],
         "keywords": ["笔记本", "白色", "硬抄"], "accessories": ["笔"]},
        {"color": "红色", "material": "纸", "brand": None, "model": None,
         "public_features": ["红色封面", "书脚有磨损"],
         "hidden_features": ["书内夹有一张课程表", "第 15 页边角有咖啡渍"],
         "keywords": ["教材", "大学物理", "红色"], "accessories": ["笔记本"]},
        {"color": "棕色", "material": "皮革", "brand": "无印良品", "model": None,
         "public_features": ["皮面笔记本", "绑带有磨损"],
         "hidden_features": ["扉页写有一句英文短句", "内页夹着一张电影票根"],
         "keywords": ["笔记本", "棕色", "皮面"], "accessories": ["笔", "卡套"]},
    ],
    "校园卡": [
        {"color": "蓝色", "material": "塑料", "brand": None, "model": None,
         "public_features": ["卡面有透明卡套", "卡套有划痕"],
         "hidden_features": ["学号 2023XXXXXX，姓名 张某某", "卡套背面贴有一张校徽贴纸"],
         "keywords": ["校园卡", "蓝色", "卡套"], "accessories": ["卡套", "挂绳"]},
        {"color": "白色", "material": "塑料", "brand": None, "model": None,
         "public_features": ["卡面有姓名贴", "边缘有磨损"],
         "hidden_features": ["学号 2022XXXXXX，姓名 王某某", "卡片背面写有宿舍楼号"],
         "keywords": ["校园卡", "白色", "一卡通"], "accessories": ["卡套"]},
        {"color": "绿色", "material": "塑料", "brand": None, "model": None,
         "public_features": ["卡面卡通贴纸", "卡角有折痕"],
         "hidden_features": ["学号 2024XXXXXX，姓名 陈某某", "卡套内夹有一张便利店小票"],
         "keywords": ["校园卡", "绿色", "贴纸"], "accessories": ["卡套", "挂绳"]},
        {"color": "红色", "material": "塑料", "brand": None, "model": None,
         "public_features": ["红色卡套", "卡面有划痕"],
         "hidden_features": ["学号 2021XXXXXX，姓名 刘某某", "卡套内侧写了手机号后四位"],
         "keywords": ["校园卡", "红色", "证件"], "accessories": ["卡套"]},
        {"color": "黄色", "material": "塑料", "brand": None, "model": None,
         "public_features": ["黄色卡套", "贴有卡通表情贴纸"],
         "hidden_features": ["学号 2023XXXXXX，姓名 赵某某", "卡套背面印有社团标识"],
         "keywords": ["校园卡", "黄色", "社团"], "accessories": ["卡套", "挂绳"]},
    ],
    "雨伞": [
        {"color": "黑色", "material": "尼龙", "brand": "天堂伞", "model": "3311E",
         "public_features": ["折叠伞", "伞面有磨损"],
         "hidden_features": ["伞骨第三节有一处弯折", "伞柄内侧刻有「L.Y.」缩写"],
         "keywords": ["折叠伞", "黑色", "雨伞"], "accessories": ["伞套"]},
        {"color": "蓝色", "material": "尼龙", "brand": "天堂伞", "model": "336T",
         "public_features": ["长柄伞", "伞面有褪色"],
         "hidden_features": ["伞柄缠绕了一圈蓝色胶带", "伞面内侧写有姓名"],
         "keywords": ["长柄伞", "蓝色", "雨伞"], "accessories": ["伞套"]},
        {"color": "粉色", "material": "尼龙", "brand": None, "model": None,
         "public_features": ["折叠伞", "伞套有污渍"],
         "hidden_features": ["伞面内侧贴有一个小猫贴纸", "伞骨末端缺一个塑料帽"],
         "keywords": ["折叠伞", "粉色", "轻便"], "accessories": ["伞套"]},
        {"color": "灰色", "material": "尼龙", "brand": "迪卡侬", "model": "QUECHUA-100",
         "public_features": ["自动伞", "伞柄有划痕"],
         "hidden_features": ["伞柄底部贴有条形码残胶", "伞面有一处缝补痕迹"],
         "keywords": ["自动伞", "灰色", "户外"], "accessories": ["伞套"]},
        {"color": "紫色", "material": "尼龙", "brand": None, "model": None,
         "public_features": ["折叠伞", "伞带松脱"],
         "hidden_features": ["伞面内侧写有「2023 校运会」", "伞柄挂着一个木质挂件"],
         "keywords": ["折叠伞", "紫色", "挂件"], "accessories": ["伞套", "挂件"]},
    ],
    "背包": [
        {"color": "黑色", "material": "帆布", "brand": "迪卡侬", "model": "NH500",
         "public_features": ["双肩包", "肩带处有磨损"],
         "hidden_features": ["主袋内衬有一道蓝色墨水渍", "肩带内侧写有姓名缩写"],
         "keywords": ["双肩包", "黑色", "电脑包"], "accessories": ["行李牌"]},
        {"color": "灰色", "material": "尼龙", "brand": "小米", "model": "Mi Casual",
         "public_features": ["双肩包", "拉链头有划痕"],
         "hidden_features": ["侧袋里有一张旧电影票根", "拉链头上系着一根红绳"],
         "keywords": ["双肩包", "灰色", "通勤"], "accessories": ["行李牌", "钥匙"]},
        {"color": "蓝色", "material": "帆布", "brand": None, "model": None,
         "public_features": ["双肩包", "正面有卡通贴布"],
         "hidden_features": ["内袋缝有一块姓名布标", "背板夹层有一张校园地图"],
         "keywords": ["双肩包", "蓝色", "贴布"], "accessories": ["挂绳"]},
        {"color": "棕色", "material": "皮革", "brand": "无印良品", "model": "MJ-BAG-02",
         "public_features": ["皮质背包", "扣带有磨损"],
         "hidden_features": ["内衬写有「2019.09」字样", "侧袋有一个皮质小挂件"],
         "keywords": ["背包", "棕色", "皮面"], "accessories": ["行李牌"]},
        {"color": "绿色", "material": "尼龙", "brand": None, "model": None,
         "public_features": ["登山包", "腰带有磨损"],
         "hidden_features": ["腰包内有一枚校徽徽章", "顶包拉链上挂着一个登山扣"],
         "keywords": ["登山包", "绿色", "户外"], "accessories": ["挂绳", "钥匙"]},
    ],
}

_ITEMS_PER_CATEGORY = 10        # 每类 10 件失物 + 10 件拾物 → 共 120 条（lost 60 / found 60）
_CORRECT_OFFSET = 0             # 正样本：found 与 lost 同序号（1001↔2001）
_HARD_NEGATIVE_OFFSET = 3       # 困难负样本：同类同色，但变体错位 3 个（品牌/细节不同）
_HARD_NEGATIVE_VARIANT = 3      # 变体错位 3 → 颜色相同（5 个变体一轮回），品牌与特征不同

#: 正样本的时间差（小时）：0.5/1/3 小时为「高时间分」，24 小时为中等，覆盖分段函数
POSITIVE_TIME_DIFFS: Tuple[float, ...] = (0.5, 1.0, 3.0, 8.0, 18.0, 24.0)
#: 困难负样本的时间差（小时）：时间上很接近，逼迫算法靠细节区分
HARD_NEGATIVE_TIME_DIFFS: Tuple[float, ...] = (0.5, 1.0, 3.0, 8.0)
#: 普通负样本的时间差（小时）：覆盖 24~72h 与 >72h 两个低档
NEGATIVE_TIME_DIFFS: Tuple[float, ...] = (30.0, 72.0, 120.0, 240.0)

_LOST_TITLE_SUFFIX = ("", "（二代）", "（旧款）", "（备用）", "（同学代管）", "（用了两年）", "（较新）",
                      "（常用）", "（贴了贴纸）", "（有磕碰）", "（刚买不久）", "（室友转赠）", "（毕业季遗留）")
_FOUND_TITLE_SUFFIX = ("", "（在自习区捡到）", "（放在服务台）", "（门卫代收）", "（同学交到值班室）",
                       "（保洁阿姨捡到）", "（讲台抽屉内）", "（座位下捡到）", "（已登记）",
                       "（无人认领）", "（暂存宿管处）", "（操场捡到）", "（图书馆捡到）")

# 常见地点串 + 确定性配对（全部来自 config/locations.yaml 的层级表）
# 正样本用「同一具体地点 / 同建筑不同楼层」；困难负样本使用相邻建筑等更弱的档位，
# 避免「地点完全一致」把困难负样本的分数顶上去。
SAME_SPOT_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("东湖校区图书馆三楼阅览室", "东湖校区图书馆三楼阅览室"),
    ("东湖校区第三食堂一楼门口", "东湖校区第三食堂一楼门口"),
    ("西山校区西区图书室一楼阅览室", "西山校区西区图书室一楼阅览室"),
    ("东湖校区大学生活动中心一楼服务台", "东湖校区大学生活动中心一楼服务台"),
)
SAME_BUILDING_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("东湖校区第一教学楼二楼教室", "东湖校区第一教学楼三楼走廊"),
    ("东湖校区图书馆二楼阅览室", "东湖校区图书馆三楼阅览室"),
    ("西山校区实验楼三楼实验室", "西山校区实验楼四楼电梯口"),
    ("西山校区三号宿舍楼二楼门口", "西山校区三号宿舍楼三楼门口"),
)
NEAR_LOCATION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("东湖校区第三食堂门口", "东湖校区第一教学楼门口"),
    ("东湖校区大学生活动中心一楼服务台", "东湖校区图书馆一楼门口"),
    ("东湖校区体育馆一楼球馆", "东湖校区大学生活动中心二楼报告厅"),
    ("西山校区第一食堂一楼门口", "西山校区工程训练中心门口"),
    ("西山校区三号宿舍楼二楼门口", "西山校区第一食堂二楼就餐区"),
    ("西山校区西区图书室一楼阅览室", "西山校区三号宿舍楼一楼门口"),
    ("东湖校区一号宿舍楼一楼门口", "东湖校区第三食堂二楼就餐区"),
    ("东湖校区田径场入口", "东湖校区体育馆一楼门口"),
)
#: 困难负样本轮换使用的地点对：同校区但**邻接图中不相邻**的建筑（地点维度不占便宜）
HARD_NEGATIVE_LOCATION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("东湖校区图书馆三楼阅览室", "东湖校区田径场入口"),        # 图书馆 ↔ 田径场：2 跳
    ("东湖校区第三食堂门口", "东湖校区体育馆一楼球馆"),        # 三食堂 ↔ 体育馆：2 跳
    ("东湖校区第一教学楼二楼教室", "东湖校区一号宿舍楼一楼门口"),  # 一教 ↔ 一舍：2 跳
    ("西山校区实验楼三楼实验室", "西山校区西操场门口"),        # 实验楼 ↔ 西操场：2 跳
    ("西山校区第一食堂一楼门口", "西山校区西区图书室一楼阅览室"),  # 一食堂 ↔ 西区图书室：2 跳
    ("西山校区三号宿舍楼二楼门口", "西山校区工程训练中心一楼车间"),  # 三舍 ↔ 工训中心：2 跳
)
#: 普通负样本使用的地点对（跨校区）
FAR_LOCATION_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("东湖校区图书馆三楼阅览室", "西山校区实验楼三楼实验室"),
    ("东湖校区第三食堂门口", "西山校区第一食堂一楼门口"),
    ("东湖校区大学生活动中心一楼服务台", "西山校区西区图书室一楼阅览室"),
    ("东湖校区体育馆一楼球馆", "西山校区工程训练中心二楼车间"),
    ("西山校区三号宿舍楼二楼门口", "东湖校区一号宿舍楼一楼门口"),
    ("西山校区校医院一楼服务台", "东湖校区田径场入口"),
    ("东湖校区第一教学楼二楼教室", "西山校区实验楼五楼"),
    ("西山校区西区图书室二楼阅览室", "东湖校区图书馆四楼阅览室"),
)
#: 正样本轮换使用的地点对（同地点 / 同建筑不同层）
POSITIVE_LOCATION_PAIRS: Tuple[Tuple[str, str], ...] = SAME_SPOT_PAIRS + SAME_BUILDING_PAIRS

_POSITIVE_TIMES = ("08:10", "12:40", "15:00", "17:35", "19:20", "21:05")
_NEGATIVE_TIMES = ("07:25", "14:15", "20:45")

#: 物品描述的统一结尾（失物/拾物共用，避免发文措辞干扰图像/文本比对）
_CLOSING_SENTENCE = "如见到请通过平台联系，可提供更多细节用于核对"

#: 类别层面的通用描述（同类别共享，用于制造「表面相似」的困难负样本）
CATEGORY_BASE_DESCRIPTIONS: Dict[str, str] = {
    "无线耳机": "无线蓝牙耳机，带充电盒，可以正常连接手机使用",
    "水杯": "日常饮水用的杯子，容量约 400 毫升，可以正常装水",
    "书籍": "本学期在用的教材或笔记本，书页完整、有课堂笔记痕迹",
    "校园卡": "校园一卡通，可刷卡进出宿舍与食堂，卡面有照片",
    "雨伞": "日常通勤用的雨伞，伞面遮雨正常，伞骨完好",
    "背包": "上学通勤用的双肩背包，主袋可放 14 寸笔记本",
}

# 校园卡/证件类必须使用虚构信息
FICTIONAL_NOTICE = "本数据集全部为人工合成的虚构信息，学号/姓名/卡号均不指向任何真实个人。"


@dataclass(frozen=True)
class ItemRecipe:
    """一条演示物品的全部素材。"""

    item_id: int
    post_type: str
    category: str
    variant_index: int
    variant: Dict[str, Any]
    title: str
    location: str
    event_time: str
    public_features: List[str]
    hidden_features: List[str]


def _variant(category: str, index: int) -> Dict[str, Any]:
    variants = VARIANTS[category]
    return variants[index % len(variants)]


def _item_location(post_type: str, index: int) -> str:
    near = NEAR_LOCATION_PAIRS[index % len(NEAR_LOCATION_PAIRS)]
    far = FAR_LOCATION_PAIRS[index % len(FAR_LOCATION_PAIRS)]
    if post_type == "lost":
        return near[0] if index % 4 != 3 else far[0]
    return near[1] if index % 4 != 3 else far[1]


def _event_time(post_type: str, index: int, category_index: int) -> str:
    base = datetime.strptime(BASE_TIME, "%Y-%m-%dT%H:%M:%S")
    days = (index * 3 + category_index * 5) % 28
    moment = base + timedelta(days=days)
    clock = 8 + (index * 3) % 12
    minute = (index * 17) % 60
    if post_type == "found":
        # 拾物登记时间略晚于失物（0~2 天），使时间差分布自然
        moment = moment + timedelta(hours=1 + (index % 5))
    return moment.replace(hour=clock, minute=minute, second=0).strftime("%Y-%m-%dT%H:%M:%S")


def build_item_recipes() -> List[ItemRecipe]:
    """生成 78 条物品素材（确定性）。"""
    recipes: List[ItemRecipe] = []
    for category_index, category in enumerate(CATEGORIES):
        for offset in range(_ITEMS_PER_CATEGORY):
            lost_index = category_index * _ITEMS_PER_CATEGORY + offset
            found_index = lost_index + _CORRECT_OFFSET
            variant_index = lost_index % len(VARIANTS[category])
            lost_variant = _variant(category, variant_index)
            found_variant = _variant(category, variant_index)  # 同一件物品：属性一致
            recipes.append(ItemRecipe(
                item_id=1000 + lost_index + 1,
                post_type="lost",
                category=category,
                variant_index=variant_index,
                variant=lost_variant,
                title=f"{category}{_LOST_TITLE_SUFFIX[offset % len(_LOST_TITLE_SUFFIX)]}",
                location=_item_location("lost", lost_index),
                event_time=_event_time("lost", lost_index, category_index),
                public_features=list(lost_variant["public_features"]),
                hidden_features=list(lost_variant["hidden_features"]),
            ))
            recipes.append(ItemRecipe(
                # found 编号与 lost 同序号（1001↔2001、1002↔2002 …），便于对照
                item_id=2000 + lost_index + 1,
                post_type="found",
                category=category,
                variant_index=variant_index,
                variant=found_variant,
                title=f"{category}{_FOUND_TITLE_SUFFIX[offset % len(_FOUND_TITLE_SUFFIX)]}",
                location=_item_location("found", found_index),
                event_time=_event_time("found", found_index, category_index),
                public_features=list(found_variant["public_features"]),
                hidden_features=list(found_variant["hidden_features"]),
            ))
    return recipes


def _description(recipe: ItemRecipe) -> str:
    variant = recipe.variant
    parts: List[str] = []
    color = variant.get("color") or ""
    brand = variant.get("brand")
    material = variant.get("material") or ""
    model = variant.get("model")
    parts.append(f"{color}{brand or ''}{model or ''}{recipe.category}".strip())
    # 类别层面的通用描述（同类物品共享）→ 让困难负样本保持「表面相似」
    parts.append(CATEGORY_BASE_DESCRIPTIONS[recipe.category])
    if material:
        parts.append(f"材质看起来是{material}")
    for feature in recipe.public_features:
        parts.append(feature)
    parts.append(_CLOSING_SENTENCE)
    return "，".join(parts) + "。"


def _image_url(recipe: ItemRecipe) -> str:
    # 演示用占位图地址：文件名带类别与序号，便于 MockImageEmbedder 稳定哈希
    code = {
        "无线耳机": "earbuds", "水杯": "bottle", "书籍": "book",
        "校园卡": "campus-card", "雨伞": "umbrella", "背包": "backpack",
    }[recipe.category]
    return (f"https://cdn.example-campus.invalid/demo/{code}/"
            f"{recipe.post_type}-{recipe.item_id:04d}-v{recipe.variant_index}.jpg")


def _ai_attributes(recipe: ItemRecipe) -> Dict[str, Any]:
    variant = recipe.variant
    keywords: List[str] = []
    for word in [recipe.category, variant.get("color"), variant.get("brand"),
                 variant.get("material"), *(variant.get("keywords") or [])]:
        if word and word not in keywords:
            keywords.append(str(word))
    uncertainties: List[str] = []
    if not variant.get("brand"):
        uncertainties.append("品牌标识不清晰")
    if not variant.get("model"):
        uncertainties.append("未识别到明确型号")
    return {
        "category": recipe.category,
        "brand": variant.get("brand"),
        "model": variant.get("model"),
        "color": variant.get("color"),
        "material": variant.get("material"),
        "visible_features": list(recipe.public_features),
        "accessories": list(variant.get("accessories") or []),
        "keywords": keywords,
        "uncertainties": uncertainties,
        "source": "rule-based-extractor(baseline-1.0)",
        "note": "演示数据中的 ai_attributes 与规则抽取器的输出形态一致，便于离线比对。",
    }


def build_demo_items() -> List[Dict[str, Any]]:
    """构造 demo_items.json 的内容。"""
    items: List[Dict[str, Any]] = []
    for recipe in build_item_recipes():
        variant = recipe.variant
        items.append({
            "id": recipe.item_id,
            "post_type": recipe.post_type,
            "category": recipe.category,
            "title": recipe.title,
            "description": _description(recipe),
            "color": variant.get("color"),
            "brand": variant.get("brand"),
            "location": recipe.location,
            "event_time": recipe.event_time,
            "public_features": list(recipe.public_features),
            "hidden_features": list(recipe.hidden_features),
            "ai_attributes": _ai_attributes(recipe),
            "image_url": _image_url(recipe),
            "material": variant.get("material"),
            "model": variant.get("model"),
            "accessories": list(variant.get("accessories") or []),
            "keywords": list(variant.get("keywords") or []),
            "synthetic": True,
            "variant_index": recipe.variant_index,
        })
    return items


# ---------------------------------------------------------------------------
# 匹配样本（正/困难负/负）
# ---------------------------------------------------------------------------
def _expected_range(score: float, margin: float) -> List[float]:
    low = max(0.0, round(score - margin, 2))
    high = min(1.0, round(score + margin, 2))
    return [low, high]


def _pick_pair(index: int, pool: Sequence[Tuple[str, str]], kind: str) -> Tuple[str, str]:
    pairs = NEAR_LOCATION_PAIRS if kind == "near" else FAR_LOCATION_PAIRS
    return pairs[index % len(pairs)]


def _time_shift(timestamp: str, hours: float, clock: str = "") -> str:
    moment = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
    shifted = moment + timedelta(hours=hours)
    if clock:
        hour, _, minute = clock.partition(":")
        shifted = shifted.replace(hour=int(hour), minute=int(minute), second=0)
    return shifted.strftime("%Y-%m-%dT%H:%M:%S")


def _hard_negative_pair(category: str, variant_index: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """构造「同类别 + 同颜色，但品牌/型号与隐藏细节不同」的困难负样本属性。

    关键点：把颜色强行对齐（外观相似），但在品牌/型号、公开特征、隐藏特征上错开，
    因此这类样本比普通负样本难判，用来检验算法是否真的在比对细节而不是只看颜色。
    """
    variant = VARIANTS[category][variant_index % len(VARIANTS[category])]
    other = VARIANTS[category][(variant_index + _HARD_NEGATIVE_VARIANT) % len(VARIANTS[category])]
    lost_view = {
        "color": variant.get("color"),
        "brand": variant.get("brand"),
        "model": variant.get("model"),
        "public_features": list(variant["public_features"]),
        "hidden_features": list(variant["hidden_features"]),
    }
    found_view = {
        "color": variant.get("color"),          # 颜色完全一致，制造干扰
        "brand": other.get("brand"),
        "model": other.get("model"),
        "public_features": list(other["public_features"]),
        "hidden_features": list(other["hidden_features"]),
    }
    if found_view["public_features"] == lost_view["public_features"]:
        found_view["public_features"] = ["外观相近但缺少对方描述的具体细节"]
    return lost_view, found_view


def _hours_text(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} 分钟"
    if hours < 48:
        return f"{hours:g} 小时"
    return f"{hours / 24:g} 天"


def build_demo_matches(
    items: Optional[Sequence[Dict[str, Any]]] = None,
    time_diff_resolver: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """构造 demo_matches.json 的内容（24 正 + 24 负，其中 12 条是 hard_negative）。

    ``time_diff_resolver`` 允许调用方在生成阶段用真实评分逻辑校验样本，
    默认不做处理（前向引用由 :mod:`scripts.generate_demo_data` 负责）。
    """
    items = list(items or build_demo_items())
    by_id = {int(item["id"]): item for item in items}
    per_category = _ITEMS_PER_CATEGORY

    def lost_id(category_index: int, offset: int) -> int:
        return 1001 + category_index * per_category + offset

    def found_id(category_index: int, offset: int) -> int:
        return 2001 + category_index * per_category + offset

    def found_time(lost_item: Dict[str, Any], hours: float) -> str:
        moment = datetime.strptime(lost_item["event_time"], "%Y-%m-%dT%H:%M:%S")
        return (moment + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")

    matches: List[Dict[str, Any]] = []
    sample = 0
    for category_index, category in enumerate(CATEGORIES):
        # ---------------- 正样本：4 组/类 ----------------
        for slot in range(4):
            offset = slot * 2                       # 0,2,4,6 → 变体各不相同
            lost = by_id[lost_id(category_index, offset)]
            found = by_id[found_id(category_index, offset)]
            diff = POSITIVE_TIME_DIFFS[(category_index + slot) % len(POSITIVE_TIME_DIFFS)]
            if slot == 3:                           # 每组留一条「时间差较大」的正样本
                diff = POSITIVE_TIME_DIFFS[-1]
            location_lost, location_found = _pick_pair(
                category_index * 3 + slot, POSITIVE_LOCATION_PAIRS, "near")
            sample += 1
            matches.append({
                "sample_id": f"M{sample:03d}",
                "label": "positive",
                "lost_item_id": lost["id"],
                "found_item_id": found["id"],
                "category": category,
                "expected_total_score_range": [-1.0, -1.0],   # 由生成脚本按真实分值回填
                "time_diff_hours": round(diff, 2),
                "location_lost": location_lost,
                "location_found": location_found,
                "found_event_time": found_time(lost, diff),
                "notes": f"同一件物品：类别、颜色、品牌、公开特征均一致，登记时间相差约 {_hours_text(diff)}。",
            })

        # ---------------- 困难负样本：2 组/类 ----------------
        for hard_index, (lost_offset, found_offset) in enumerate(((0, _HARD_NEGATIVE_OFFSET), (1, _HARD_NEGATIVE_OFFSET + 1))):
            lost = by_id[lost_id(category_index, lost_offset)]
            found = by_id[found_id(category_index, found_offset)]
            lost_view, found_view = _hard_negative_pair(category, lost["variant_index"])
            diff = HARD_NEGATIVE_TIME_DIFFS[(category_index + hard_index) % len(HARD_NEGATIVE_TIME_DIFFS)]
            location_lost, location_found = _pick_pair(
                category_index * 2 + hard_index, HARD_NEGATIVE_LOCATION_PAIRS, "near")
            sample += 1
            matches.append({
                "sample_id": f"M{sample:03d}",
                "label": "hard_negative",
                "lost_item_id": lost["id"],
                "found_item_id": found["id"],
                "category": category,
                "expected_total_score_range": [-1.0, -1.0],
                "time_diff_hours": round(diff, 2),
                "location_lost": location_lost,
                "location_found": location_found,
                "found_event_time": found_time(lost, diff),
                "conflict_fields": ["brand", "public_features", "hidden_features"],
                "notes": "同类别、同颜色，但品牌/型号与隐藏细节不同（外观相似的另一件物品）。",
                "lost_view": lost_view,
                "found_view": found_view,
            })

        # ---------------- 普通负样本：4 组/类 ----------------
        negative_offset = per_category - 1
        for slot in range(4):
            lost = by_id[lost_id(category_index, negative_offset - slot)]
            other_category_index = (category_index + 1 + slot) % len(CATEGORIES)
            found = by_id[found_id(other_category_index, (negative_offset - slot + 2) % per_category)]
            diff = NEGATIVE_TIME_DIFFS[(category_index + slot) % len(NEGATIVE_TIME_DIFFS)]
            location_lost, location_found = _pick_pair(category_index + slot, FAR_LOCATION_PAIRS, "far")
            sample += 1
            matches.append({
                "sample_id": f"M{sample:03d}",
                "label": "negative",
                "lost_item_id": lost["id"],
                "found_item_id": found["id"],
                "category": category,
                "expected_total_score_range": [-1.0, -1.0],
                "time_diff_hours": round(diff, 2),
                "location_lost": location_lost,
                "location_found": location_found,
                "found_event_time": found_time(lost, diff),
                "notes": f"类别不同（{lost['category']} vs {found['category']}），时间差约 {_hours_text(diff)}，且不在同一校区。",
            })
    return matches


def build_demo_dataset() -> Dict[str, Any]:
    """一次性返回两份数据（供生成脚本与校验脚本使用）。"""
    items = build_demo_items()
    matches = build_demo_matches(items)
    return {
        "items": items,
        "matches": matches,
        "meta": {
            "model_version": MODEL_VERSION,
            "generated_by": "algorithm/scripts/generate_demo_data.py",
            "base_time": BASE_TIME,
            "deterministic": True,
            "license": "自由使用（CC0 风格）；全部为人工合成数据，不含真实隐私信息。",
        },
    }


# ---------------------------------------------------------------------------
# 运行期辅助：直接拿演示数据当接口入参
# ---------------------------------------------------------------------------
def get_demo_query(post_type: str = "lost", index: int = 0) -> Dict[str, Any]:
    """取一条演示物品作为 match 接口的 query_item。"""
    items = [item for item in build_demo_items() if item["post_type"] == post_type]
    return items[index % len(items)] if items else {}


__all__ = [
    "MODEL_VERSION",
    "BASE_TIME",
    "CATEGORIES",
    "VARIANTS",
    "FICTIONAL_NOTICE",
    "ItemRecipe",
    "build_item_recipes",
    "build_demo_items",
    "build_demo_matches",
    "build_demo_dataset",
    "get_demo_query",
]

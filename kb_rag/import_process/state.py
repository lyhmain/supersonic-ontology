"""导入流程图状态定义（沿用原版）"""

from typing import TypedDict


class ImportGraphState(TypedDict):
    """图的状态定义，包含所有节点产生和消费的数据字段"""

    task_id: str               # 任务唯一ID，用于追踪日志

    # 流程控制标记
    is_md_read_enabled: bool   # 是否启用 Markdown 读取路径
    is_pdf_read_enabled: bool  # 是否启用 PDF 读取路径

    # 路径相关
    local_dir: str             # 当前工作目录或输出目录
    local_file_path: str       # 原始输入文件路径
    file_title: str            # 文件标题（文件名去后缀）
    pdf_path: str              # PDF 文件路径 (如果输入是PDF)
    md_path: str               # Markdown 文件路径 (转换后或直接输入的)

    # 内容数据
    md_content: str            # Markdown 的全文内容
    chunks: list               # 切片列表
    item_name: str             # 识别主体的名称（例如：万用表）

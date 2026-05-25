from firmware.core.vendor.uQR import ERROR_CORRECT_M, QRCode

_BG = 0x0000
_FG = 0xFFFF


def make_qr(data: str, error_correction=ERROR_CORRECT_M):
    qr = QRCode(error_correction=error_correction)
    qr.add_data(data)
    return qr.get_matrix()


def fit_module_px(n_modules: int, max_w: int, max_h: int) -> int:
    return max(1, min(max_w, max_h) // n_modules)


def draw_qr(hal, matrix, x0: int, y0: int, module_px: int = 3) -> None:
    n = len(matrix)
    hal.fill_rect(x0, y0, n * module_px, n * module_px, _FG)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                hal.fill_rect(
                    x0 + c * module_px, y0 + r * module_px,
                    module_px, module_px, _BG,
                )

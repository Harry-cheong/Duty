import gspread

class GSheet():
    def __init__(self, sh: gspread.Spreadsheet, ws: gspread.Worksheet):
        self.sh = sh
        self.ws = ws

        self.batch_requests = [] # Store all the requests to execute as one API call
    
    
    def rgb(self, red, green, blue):
        return {"red": round(red/255, 3), "green": round(green/255, 3), "blue": round(blue/255, 3)}
    

    def set_width(self, start_col: int, size: int, end_col: int = None):
        if not end_col: end_col = start_col + 1
        self.batch_requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": self.ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,  # column A
                    "endIndex": end_col    # exclusive
                },
                "properties": {"pixelSize": size},
                "fields": "pixelSize"
             }
        })
    

    def set_auto_width(self, start_col: int, end_col: int = None):
        if not end_col: end_col = start_col + 1
        self.batch_requests.append({
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": self.ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col
                }
            }
        })
    

    def freeze(self, cols: int = None, rows: int = None):
        _grid_properties = {}
        _fields = ""

        if cols:
            _grid_properties["frozenColumnCount"] = cols
            _fields += "gridProperties.frozenColumnCount,"
        
        if cols:
            _grid_properties["frozenRowCount"] = rows
            _fields += "gridProperties.frozenRowCount,"
        
        self.batch_requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": self.ws.id,
                    "gridProperties": _grid_properties
                },
                "fields": _fields
            }
        })
    

    def set_height(self, start_row: int, size: int, end_row: int = None):
        if not end_row: end_row = start_row+1
        self.batch_requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": self.ws.id,
                    "dimension": "ROWS",
                    "startIndex": start_row,   
                    "endIndex": end_row     
                },
                "properties": {
                    "pixelSize": size
                },
                "fields": "pixelSize"
            }
        })
    
    def col_letter(self, n):
        '''Convert 0-indexed column number to spreadsheet letter (0=A, 1=B, ...)'''
        result = ""
        n += 1  # 1-indexed
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            result = chr(65 + remainder) + result
        return result
    

    def merge_cells(self, start_row, start_col, end_row = None, end_col=None, merge_type="MERGE_ALL"):
        '''
        MERGE_ALL — merges the entire range into one cell
        MERGE_COLUMNS — merges each column in the range separately (e.g., A1:C2 becomes 3 merged cells, each spanning rows 1-2)
        MERGE_ROWS — merges each row in the range separately (e.g., A1:C2 becomes 2 merged cells, each spanning columns A-C)
        '''
        if not end_row: end_row = start_row + 1
        if not end_col: end_col = start_col + 1
        self.batch_requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": self.ws.id,
                    "startRowIndex": start_row,    
                    "endRowIndex": end_row,   
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col  
                },
                "mergeType": merge_type
            }
        })
    

    def format_cells(self, start_row=None, start_col=None, end_row = None, end_col=None, fill_colour=None, bold=False, horiz_align="LEFT", wrap="OVERFLOW_CELL", vert_align="MIDDLE"):
        '''
        If no start_row/end_row, entire column is formatted
        '''
        _range_dict = {
            "sheetId": self.ws.id
        }
        if start_row:
            _range_dict["startRowIndex"] = start_row
        if end_row:
            _range_dict["endRowIndex"] = end_row
        if start_col:
            _range_dict["startColumnIndex"] = start_col
        if end_col:
            _range_dict["endColumnIndex"] = end_col

        if not fill_colour:
            fill_colour = None
        self.batch_requests.append({
            "repeatCell": {
                "range": _range_dict,
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": fill_colour,
                        "textFormat": {"bold": bold},
                        "horizontalAlignment": horiz_align,
                        "wrapStrategy": wrap,
                        "verticalAlignment": vert_align
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat.bold,horizontalAlignment,wrapStrategy,verticalAlignment)"
            }
        })

    
    def execute_req(self):
        self.sh.batch_update({
            "requests": self.batch_requests
        })
        self.batch_requests = []
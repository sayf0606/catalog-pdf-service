#!/usr/bin/env python3
"""
Catalog PDF Filter Script for n8n
Usage: python3 catalog_filter.py <pdf_path> <product_numbers> <output_path>
  product_numbers: comma-separated list of NO. values, e.g. "1,5,14,23"
"""
 
import sys
import os
import io
import json
import tempfile
import pdfplumber
from pdf2image import convert_from_path
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, Image as RLImage, KeepTogether)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
 
SCALE = 150 / 72  # 150 DPI / 72 pts per inch
 
def extract_products(pdf_path):
    """Extract all product data with row bounding boxes"""
    products = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            table_settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
            found_tables = page.find_tables(table_settings)
            if not found_tables:
                continue
            
            main_table = found_tables[0]
            rows_data = main_table.extract()
            rows_bbox = main_table.rows
            
            # Collect product rows
            product_rows = []
            for i, (row_data, row_obj) in enumerate(zip(rows_data, rows_bbox)):
                if not row_data or not row_data[0]:
                    continue
                no_val = str(row_data[0]).strip()
                if not no_val.isdigit():
                    continue
                product_rows.append((row_data, row_obj))
            
            for idx, (row_data, row_obj) in enumerate(product_rows):
                row_top = row_obj.bbox[1]
                if idx + 1 < len(product_rows):
                    row_bottom = product_rows[idx + 1][1].bbox[1]
                else:
                    row_bottom = row_obj.bbox[3]
                
                # Parse fields - handle mixed columns
                no_val = str(row_data[0]).strip()
                oem = str(row_data[1]).strip() if row_data[1] else ''
                
                # item_name may be truncated due to column overflow
                item_name_raw = str(row_data[2]).strip() if row_data[2] else ''
                car_model = str(row_data[3]).strip() if row_data[3] else ''
                
                # QTY might be mixed with car_model text
                qty_raw = str(row_data[4]).strip() if row_data[4] else ''
                qty = '10'  # default, try to parse
                for part in qty_raw.split():
                    if part.isdigit():
                        qty = part
                        break
                
                price_pcs = str(row_data[5]).strip() if row_data[5] else ''
                total = str(row_data[6]).strip() if row_data[6] else ''
                price_rub = str(row_data[8]).strip() if len(row_data) > 8 and row_data[8] else ''
                
                products.append({
                    'no': no_val,
                    'oem': oem,
                    'item_name': item_name_raw,
                    'car_model': car_model,
                    'qty': qty,
                    'price_pcs': price_pcs,
                    'total': total,
                    'price_rub': price_rub,
                    'page': page_num,
                    'row_top': row_top,
                    'row_bottom': row_bottom,
                    'table_left': row_obj.bbox[0],
                    'table_right': row_obj.bbox[2],
                })
    
    return products
 
 
def get_row_image(page_images, page_num, row_top, row_bottom, table_left, table_right):
    """Crop row region from rendered page image (dict keyed by page_num), return PIL Image"""
    if page_num not in page_images:
        return None
    
    page_img = page_images[page_num]
    left_px = int(table_left * SCALE)
    top_px = int(row_top * SCALE)
    right_px = int(table_right * SCALE)
    bottom_px = int(row_bottom * SCALE)
    
    # Clamp to image bounds
    w, h = page_img.size
    left_px = max(0, left_px)
    top_px = max(0, top_px)
    right_px = min(w, right_px)
    bottom_px = min(h, bottom_px)
    
    if bottom_px <= top_px or right_px <= left_px:
        return None
    
    return page_img.crop((left_px, top_px, right_px, bottom_px))
 
 
def create_filtered_pdf(pdf_path, selected_nos, output_path):
    """Main function: filter products by NO, create output PDF"""
    
    # Normalize: accept "1,5,14" or "1 5 14" or "1, 5, 14"
    # Parse requested numbers, preserving user's order and removing duplicates
    requested_order = []
    seen_req = set()
    for part in selected_nos.replace(',', ' ').split():
        part = part.strip()
        if part and part not in seen_req:
            seen_req.add(part)
            requested_order.append(part)
 
    # Extract all products
    print(f"Parsing catalog: {pdf_path}")
    products = extract_products(pdf_path)
    print(f"Found {len(products)} products total")
 
    # Build lookup: NO -> first matching product (catalog may repeat a NO on
    # different pages; we take the first occurrence only)
    first_by_no = {}
    for p in products:
        if p['no'] not in first_by_no:
            first_by_no[p['no']] = p
 
    # Filter in the exact order the user asked, one row per number
    filtered = [first_by_no[no] for no in requested_order if no in first_by_no]
 
    if not filtered:
        return False, f"Товары не найдены. Доступные NO: {', '.join(sorted(set(p['no'] for p in products), key=int))}"
 
    print(f"Selected {len(filtered)} products: {[p['no'] for p in filtered]}")
 
    # Render PDF pages to images (ONLY the pages that are actually needed —
    # important on low-memory hosting like Render free tier)
    needed_pages = sorted(set(p['page'] for p in filtered))
    print(f"Rendering pages: {needed_pages}")
    
    all_page_imgs = {}
    for pnum in needed_pages:
        imgs = convert_from_path(pdf_path, dpi=150, first_page=pnum + 1, last_page=pnum + 1)
        if imgs:
            all_page_imgs[pnum] = imgs[0]
    
    # Build PDF with ReportLab
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )
    
    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('Normal2', fontName='Helvetica', fontSize=8, leading=10)
    style_bold = ParagraphStyle('Bold2', fontName='Helvetica-Bold', fontSize=9, leading=12)
    style_header = ParagraphStyle('Header', fontName='Helvetica-Bold', fontSize=14, 
                                   leading=16, alignment=TA_CENTER)
    style_small = ParagraphStyle('Small', fontName='Helvetica', fontSize=7, leading=9)
    
    story = []
    
    # Title
    story.append(Paragraph("ВЫБРАННЫЕ ПОЗИЦИИ ИЗ КАТАЛОГА", style_header))
    story.append(Spacer(1, 5*mm))
    
    # Table header
    header = [
        Paragraph('<b>NO.</b>', style_bold),
        Paragraph('<b>OEM / Артикул</b>', style_bold),
        Paragraph('<b>Наименование</b>', style_bold),
        Paragraph('<b>Кол-во</b>', style_bold),
        Paragraph('<b>Цена (RMB)</b>', style_bold),
        Paragraph('<b>Итого</b>', style_bold),
        Paragraph('<b>Фото</b>', style_bold),
        Paragraph('<b>Цена (₽)</b>', style_bold),
    ]
    
    col_widths = [12*mm, 38*mm, 50*mm, 14*mm, 18*mm, 16*mm, 28*mm, 20*mm]
    
    table_data = [header]
    
    for p in filtered:
        # Get row image
        row_img = get_row_image(
            all_page_imgs, p['page'],
            p['row_top'], p['row_bottom'],
            p['table_left'], p['table_right']
        )
        
        # Extract just the PICTURE column region (col 7 is approx x=440-530 of 573 pts wide table)
        # Picture column is ~68/554 of total width from left
        img_cell = Paragraph("—", style_small)
        
        if row_img:
            # Crop only the picture portion of the row
            row_w = row_img.width
            row_h = row_img.height
            # Picture column is approximately at 77%-90% of row width
            pic_left = int(row_w * 0.74)
            pic_right = int(row_w * 0.88)
            pic_crop = row_img.crop((pic_left, 0, pic_right, row_h))
            
            # Save to buffer
            img_buf = io.BytesIO()
            pic_crop.save(img_buf, format='PNG')
            img_buf.seek(0)
            
            # Scale to fit cell
            target_h = 20*mm
            aspect = pic_crop.width / pic_crop.height if pic_crop.height > 0 else 1
            target_w = min(target_h * aspect, 25*mm)
            
            try:
                rl_img = RLImage(img_buf, width=target_w, height=target_h)
                img_cell = rl_img
            except:
                img_cell = Paragraph("фото", style_small)
        
        row = [
            Paragraph(p['no'], style_normal),
            Paragraph(p['oem'], style_small),
            Paragraph(p['item_name'][:60], style_normal),
            Paragraph(p['qty'], style_normal),
            Paragraph(p['price_pcs'], style_normal),
            Paragraph(p['total'], style_normal),
            img_cell,
            Paragraph(p['price_rub'], style_normal),
        ]
        table_data.append(row)
    
    # Create table
    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2C3E50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
        ('ROWBACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2C3E50')),
        
        # Data rows
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # NO col
        ('ALIGN', (3, 1), (5, -1), 'CENTER'),  # QTY, prices
        ('ALIGN', (6, 1), (6, -1), 'CENTER'),  # Photo
        ('ALIGN', (7, 1), (7, -1), 'RIGHT'),   # RUB price
        ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
        
        # Alternating row colors
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9FA')]),
        
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#DEE2E6')),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#2C3E50')),
        
        # Min row height
        ('ROWHEIGHT', (0, 1), (-1, -1), 22*mm),
        ('ROWHEIGHT', (0, 0), (-1, 0), 10*mm),
        
        # Padding
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    
    story.append(tbl)
    
    # Footer
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(f"Позиций в выборке: {len(filtered)}", style_normal))
    
    doc.build(story)
    print(f"Output PDF saved: {output_path}")
    return True, f"Создан PDF с {len(filtered)} позициями"
 
 
if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 catalog_filter.py <pdf_path> <numbers> <output_path>")
        print("Example: python3 catalog_filter.py catalog.pdf '1,5,14' output.pdf")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    numbers = sys.argv[2]
    output_path = sys.argv[3]
    
    success, message = create_filtered_pdf(pdf_path, numbers, output_path)
    
    result = {"success": success, "message": message, "output": output_path}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if success else 1)
 


    sys.exit(0 if success else 1)

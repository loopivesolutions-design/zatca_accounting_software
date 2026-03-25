def convert_image_to_webp(image_field):
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image

    if not image_field:
        return None, None

    original_name = image_field.name

    if original_name.lower().endswith(".webp"):
        return image_field, original_name

    # Get file size in bytes
    file_size_bytes = image_field.size if hasattr(image_field, 'size') else 0
    file_size_kb = file_size_bytes / 1024
    file_size_mb = file_size_bytes / (1024 * 1024)
    
    # If image is less than 500KB, skip compression and return original
    if file_size_kb < 500:
        return image_field, original_name
    
    # Adjust quality and max_size based on file size
    # Images <= 10MB: very high quality (minimal compression), moderate resizing
    # Images > 20MB: high compression, aggressive resizing
    # Images between 10MB and 20MB: medium quality, standard resizing
    if file_size_mb <= 10:
        quality = 95  # Very high quality, minimal compression for smaller images
        max_size = (2000, 2000)  # Moderate resizing while maintaining high quality
    elif file_size_mb > 20:
        quality = 75  # High compression for very large images
        max_size = (1200, 1200)  # Aggressive resizing for large files
    else:
        quality = 85  # Medium quality for medium-sized images
        max_size = (1600, 1600)  # Moderate resizing

    img = Image.open(image_field)
    img = img.convert("RGB")

    # Resize if max_size is specified
    if max_size:
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="WEBP", quality=quality)

    # Clean file name
    base_name = original_name.split("/")[-1].rsplit(".", 1)[0]
    new_name = f"{base_name}.webp"

    return ContentFile(buffer.getvalue()), new_name


def convert_and_save_image_field(image_field, field_name="image"):
    """
    Helper function to convert an image field to WebP and save it.
    
    Args:
        image_field: The ImageField to convert
        field_name: Name of the field (for logging)
    
    Returns:
        bool: True if conversion was successful or skipped, False if error occurred
    """
    if not image_field:
        return True
    
    try:
        # Check if this is a new file upload (has file attribute) or existing file
        is_new_upload = hasattr(image_field, 'file') and image_field.file is not None
        has_name = hasattr(image_field, 'name') and image_field.name
        
        if not (is_new_upload or has_name):
            return True
        
        # Get current filename for checking extension
        if has_name:
            current_filename = image_field.name.split("/")[-1] if "/" in image_field.name else image_field.name
        else:
            # New upload without name yet - get from file
            current_filename = getattr(image_field.file, 'name', '') if is_new_upload else ''
        
        # Only convert if not already WebP
        if not current_filename or current_filename.lower().endswith(".webp"):
            return True
        
        # Ensure file is at the beginning if it's seekable
        if is_new_upload and hasattr(image_field.file, 'seek'):
            image_field.file.seek(0)
        elif hasattr(image_field, 'seek'):
            image_field.seek(0)
        
        # Convert to WebP
        webp_file, webp_name = convert_image_to_webp(image_field)
        if webp_file and webp_name:
            # Extract only the filename, removing any path
            webp_filename = webp_name.split("/")[-1] if "/" in webp_name else webp_name
            # Save with just the filename - Django will handle the upload_to path
            image_field.save(webp_filename, webp_file, save=False)
            return True
        
        return True
    except Exception as e:
        # If conversion fails, log but don't break the save
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to convert {field_name} to WebP: {str(e)}")
        # Continue with original file if conversion fails
        return False
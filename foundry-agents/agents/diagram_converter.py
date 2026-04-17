"""
Diagram Converter Module
Extracts Mermaid diagrams from markdown and converts them to images.
Embeds the images back into the markdown document.

Uses mermaid-py library for conversion to PNG/SVG/JPG formats.
"""

import os
import re
import base64
import logging
from typing import Optional
from datetime import datetime
from io import BytesIO
from dotenv import load_dotenv
load_dotenv()

# Import mermaid library
try:
    from mermaid import Mermaid
    MERMAID_AVAILABLE = True
except ImportError:
    MERMAID_AVAILABLE = False
    logging.warning("mermaid-py not installed. Install with: pip install mermaid-py")

# PIL for image conversion
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("Pillow not installed. Install with: pip install Pillow")

# Azure Storage imports
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential

# Configure logging
logger = logging.getLogger(__name__)


def extract_and_convert_mermaid_from_markdown(
    markdown_content: str,
    app_id: str,
    output_format: str = "png",
    embed_method: str = "base64"
) -> str:
    """
    Extract Mermaid diagrams from markdown, convert to images, and embed them back.
    
    Args:
        markdown_content: Markdown content with Mermaid code blocks
        app_id: Application ID
        output_format: Output format (png, jpg, svg)
        embed_method: How to embed images ('base64' or 'url')
        
    Returns:
        Updated markdown with images embedded
    """
    if not MERMAID_AVAILABLE:
        logger.warning("Mermaid not available, returning original markdown")
        return markdown_content
    
    logger.info(f"Extracting and converting Mermaid diagrams from markdown...")
    
    # Find all Mermaid code blocks
    mermaid_pattern = r'```mermaid\n(.*?)\n```'
    matches = list(re.finditer(mermaid_pattern, markdown_content, re.DOTALL))
    
    if not matches:
        logger.info("No Mermaid diagrams found in markdown")
        return markdown_content
    
    logger.info(f"Found {len(matches)} Mermaid diagrams to convert")
    
    # Initialize storage if using URL embed method
    storage_account_url = None
    blob_service = None
    if embed_method == "url":
        storage_account_url = _get_storage_url()
        credential = SyncDefaultAzureCredential(exclude_shared_token_cache_credential=True)
        blob_service = BlobServiceClient(storage_account_url, credential=credential)
    
    # Process each diagram in reverse order (to maintain positions when replacing)
    updated_markdown = markdown_content
    for i, match in enumerate(reversed(matches)):
        diagram_index = len(matches) - i
        mermaid_code = match.group(1).strip()
        
        try:
            logger.info(f"Converting diagram {diagram_index}/{len(matches)}...")
            
            # Convert Mermaid to image
            image_data = _convert_mermaid_to_image(mermaid_code, output_format)
            
            if not image_data:
                logger.warning(f"Failed to convert diagram {diagram_index}, keeping Mermaid code")
                continue
            
            # Embed the image
            if embed_method == "base64":
                # Base64 embed
                img_base64 = base64.b64encode(image_data).decode('utf-8')
                
                # Determine MIME type
                mime_types = {
                    'png': 'image/png',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'svg': 'image/svg+xml'
                }
                mime_type = mime_types.get(output_format.lower(), 'image/png')
                
                # Create markdown image with base64 data
                img_markdown = f'![Architecture Diagram {diagram_index}](data:{mime_type};base64,{img_base64})'
                
            elif embed_method == "url":
                # Upload to blob and get URL
                blob_url = _upload_diagram_image(
                    image_data=image_data,
                    app_id=app_id,
                    diagram_name=f"diagram_{diagram_index}",
                    output_format=output_format,
                    blob_service=blob_service,
                    storage_account_url=storage_account_url
                )
                
                if blob_url:
                    img_markdown = f'![Architecture Diagram {diagram_index}]({blob_url})'
                else:
                    logger.warning(f"Failed to upload diagram {diagram_index}, keeping Mermaid code")
                    continue
            else:
                logger.warning(f"Unknown embed method: {embed_method}, keeping Mermaid code")
                continue
            
            # Replace the Mermaid code block with the image
            updated_markdown = updated_markdown[:match.start()] + img_markdown + updated_markdown[match.end():]
            logger.info(f"✅ Converted and embedded diagram {diagram_index}")
            
        except Exception as e:
            logger.error(f"Error converting diagram {diagram_index}: {e}")
            continue
    
    logger.info(f"✅ Completed converting {len(matches)} diagrams")
    return updated_markdown


def _get_storage_url() -> str:
    """Get storage account URL from environment variables"""
    account_url = (
        os.getenv("AZURE_BLOB_ACCOUNT_URL") or 
        os.getenv("AZURE_STORAGE_ACCOUNT_URL") or 
        os.getenv("AZURE_TABLES_ACCOUNT_URL") or 
        os.getenv("AZURE_TABLE_ACCOUNT_URL")
    )
    
    if account_url:
        return account_url.strip()
    
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if account_name:
        return f"https://{account_name}.blob.core.windows.net"
    
    raise ValueError(
        "Azure Storage account URL not found. "
        "Set AZURE_STORAGE_ACCOUNT_URL or AZURE_STORAGE_ACCOUNT_NAME environment variable."
    )


def _convert_mermaid_to_image(mermaid_code: str, output_format: str = "png") -> Optional[bytes]:
    """
    Convert Mermaid diagram code to image bytes.
    
    Args:
        mermaid_code: Mermaid syntax code
        output_format: Output format (png, jpg, svg)
        
    Returns:
        Image data as bytes, or None if conversion failed
    """
    try:
        # Suppress IPython warning
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*IPython.*")
            
            # Create Mermaid renderer
            mermaid = Mermaid(graph=mermaid_code)
            
            # Get image data
            if output_format.lower() in ['png', 'jpg', 'jpeg']:
                image_data = mermaid.img_response.content
                
                # Convert PNG to JPG if requested
                if output_format.lower() in ['jpg', 'jpeg'] and PIL_AVAILABLE:
                    try:
                        from PIL import Image
                        from io import BytesIO
                        
                        # Open PNG image
                        img = Image.open(BytesIO(image_data))
                        
                        # Convert RGBA to RGB if necessary
                        if img.mode in ('RGBA', 'LA', 'P'):
                            background = Image.new('RGB', img.size, (255, 255, 255))
                            if img.mode == 'P':
                                img = img.convert('RGBA')
                            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                            img = background
                        
                        # Save as JPG
                        output = BytesIO()
                        img.save(output, format='JPEG', quality=95)
                        image_data = output.getvalue()
                        
                    except Exception as jpg_error:
                        logger.warning(f"Failed to convert to JPG, using PNG: {jpg_error}")
                
            elif output_format.lower() == 'svg':
                image_data = mermaid.svg_response.text.encode('utf-8')
            else:
                logger.warning(f"Unsupported format: {output_format}, using PNG")
                image_data = mermaid.img_response.content
        
        return image_data
        
    except Exception as e:
        logger.error(f"Failed to convert Mermaid diagram: {e}")
        return None


def _upload_diagram_image(
    image_data: bytes,
    app_id: str,
    diagram_name: str,
    output_format: str,
    blob_service: BlobServiceClient,
    storage_account_url: str
) -> Optional[str]:
    """
    Upload diagram image to Azure Blob Storage.
    
    Args:
        image_data: Image data as bytes
        app_id: Application ID (used as container name)
        diagram_name: Name of the diagram
        output_format: Image format (png, jpg, svg)
        blob_service: Azure BlobServiceClient
        storage_account_url: Storage account URL
        
    Returns:
        Blob URL of the uploaded image, or None if upload failed
    """
    try:
        # Use app_id as container name
        container_name = str(app_id).lower()
        
        # Generate blob name
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        blob_name = f"diagrams/{diagram_name}_{timestamp}.{output_format.lower()}"
        
        # Create container if not exists
        container_client = blob_service.get_container_client(container_name)
        try:
            container_client.create_container()
            logger.info(f"Created container: {container_name}")
        except Exception:
            pass  # Container already exists
        
        # Determine content type
        content_type_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml"
        }
        content_type = content_type_map.get(output_format.lower(), "image/png")
        
        # Upload image
        container_client.upload_blob(
            name=blob_name,
            data=image_data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type)
        )
        
        blob_url = f"{storage_account_url.rstrip('/')}/{container_name}/{blob_name}"
        logger.info(f"✅ Diagram uploaded to: {blob_url}")
        
        return blob_url
        
    except Exception as e:
        logger.error(f"Failed to upload diagram to blob storage: {e}")
        return None

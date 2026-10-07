"""Same-origin media facade: selected account, scoped inputs, versioned outputs."""
from __future__ import annotations
import asyncio
import base64
import io
import json
import mimetypes
import subprocess
import tempfile
from itertools import islice
from zipfile import ZipFile, BadZipFile
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from termx.auth import extract_passcode
from termx.media.service import ArtifactService, MediaProvider, LIMIT


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    title: str = Field(max_length=200)
    content: object
    project_id: str | None = None


class ReviseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    content: object


class GenerateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=8,max_length=128)
    project_id: str | None = None
    operation: str
    provider_id: str
    model: str
    prompt: str = Field(default='', max_length=64000)
    input_id: str | None = None
    voice: str = Field(default='alloy',max_length=64)
    acknowledge_billing: bool = False


def image_context(raw, mime):
    from PIL import Image, ImageOps
    image = Image.open(io.BytesIO(raw))
    if image.width*image.height > 32_000_000:
        raise HTTPException(413,'Image exceeds 32 million pixels')
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((1600,1600))
    target = io.BytesIO()
    image.save(target,format='JPEG',quality=85)
    return {'name':'Selected image (resized, metadata removed)','mime':'image/jpeg',
            'data':base64.b64encode(target.getvalue()).decode()}


def input_context(service, owner, identifier):
    item = service.get(owner,identifier)
    raw,mime = service.binary(owner,identifier)
    if mime.startswith('image/'):
        return {'attachments':[image_context(raw,mime)],'text':'','disclosure':'Resized to 1600 pixels; EXIF metadata removed'}
    if mime in {'text/plain','text/markdown','application/json','text/csv'}:
        return {'attachments':[],'text':raw.decode('utf-8')[:64000],'disclosure':'Text limited to 64000 characters'}
    if mime == 'application/pdf':
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import LimitReachedError, PdfReadError
        try:
            # Context-local limits also apply to lazy text extraction. Do not
            # change global PDF parser settings shared by concurrent users.
            with apply_configuration(maximum_declared_stream_length=8_000_000,
                    array_based_stream_maximum_output_length=8_000_000,
                    zlib_maximum_output_length=8_000_000,lzw_maximum_output_length=8_000_000,
                    run_length_maximum_output_length=8_000_000,page_tree_maximum_entries=10000,
                    page_tree_maximum_depth=50,xform_maximum_invocations_per_extraction=100):
                document = PdfReader(io.BytesIO(raw))
                if document.is_encrypted:
                    raise HTTPException(400,'Encrypted PDF input requires a decrypted copy')
                parts=[];remaining=64000
                for page in islice(document.pages,50):
                    part=(page.extract_text() or '')[:remaining]
                    parts.append(part);remaining-=len(part)+1
                    if remaining<=0:break
                text='\n'.join(parts)[:64000]
        except LimitReachedError as exc:
            raise HTTPException(413,'PDF expanded contents exceed the conversion limit') from exc
        except PdfReadError as exc:
            raise HTTPException(400,'Invalid PDF input') from exc
        return {'attachments':[],'text':text,'disclosure':'First 50 pages, extracted text only; limited to 64000 characters'}
    if mime == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        # Bound expanded content before the Office parser allocates XML trees.
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries)>2000 or sum(entry.file_size for entry in entries)>50*1024*1024:
                    raise HTTPException(413,'Document expanded contents exceed the conversion limit')
        except BadZipFile as exc:
            raise HTTPException(400,'Invalid document archive') from exc
        from docx import Document
        document = Document(io.BytesIO(raw))
        return {'attachments':[],'text':'\n'.join(p.text for p in document.paragraphs)[:64000],
                'disclosure':'Document paragraphs extracted; formatting and embedded media omitted'}
    if mime.startswith('video/'):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory(prefix='termx-video-') as folder:
            source = Path(folder)/'input'
            source.write_bytes(raw)
            output = Path(folder)/'frame-%02d.jpg'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-protocol_whitelist','file,pipe',
                '-i',str(source),'-vf','fps=1/5,scale=1200:-2','-frames:v','4',str(output)],
                capture_output=True,timeout=45,check=True)
            images = [image_context(path.read_bytes(),'image/jpeg') for path in sorted(Path(folder).glob('frame-*.jpg'))]
            return {'attachments':images,'text':'','disclosure':'Up to four frames sampled every five seconds; audio omitted'}
    if mime.startswith('audio/'):
        raise HTTPException(409,'Select a configured transcription account to convert audio before attaching')
    raise HTTPException(415,'Unsupported input conversion')


def mount_media(app,state):
    state.artifacts = ArtifactService(state.delivery.directory.parent/'workspace-artifacts')
    state.media = MediaProvider(state,state.artifacts)
    router = APIRouter(prefix='/api/media')

    def actor(request, project=None, scope='agent-view'):
        raw = extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        current = state.identity.resolve(raw)
        if not current:
            raise HTTPException(401,'Managed sign-in required')
        state.authorization.require(raw,scope,project_id=project)
        return current.principal.id

    def owned(request, identifier, scope='agent-view'):
        raw = extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        current = state.identity.resolve(raw)
        if not current:
            raise HTTPException(401,'Managed sign-in required')
        item = state.artifacts.get(current.principal.id,identifier)
        state.authorization.require(raw,scope,project_id=item['project'])
        return current.principal.id,item

    @router.get('/artifacts')
    def artifacts(request: Request, project_id: str | None=None):
        return state.artifacts.list(actor(request,project_id),project_id)

    @router.get('/operations')
    def operations(request: Request, project_id: str | None=None):
        return state.artifacts.operations(actor(request,project_id),project_id)

    @router.post('/artifacts')
    def create(body: ArtifactInput,request: Request):
        return state.artifacts.create(actor(request,body.project_id,'agent-run'),body.project_id,body.kind,body.title,body.content)

    @router.get('/artifacts/{identifier}')
    def read(identifier: str,request: Request,version: int | None=None):
        owner,_=owned(request,identifier)
        return state.artifacts.get(owner,identifier,version)

    @router.put('/artifacts/{identifier}')
    def revise(identifier: str,body: ReviseInput,request: Request):
        owner,_=owned(request,identifier,'agent-run')
        return state.artifacts.revise(owner,identifier,body.version,content=body.content)

    @router.get('/artifacts/{identifier}/versions')
    def versions(identifier: str,request: Request):
        owner,_=owned(request,identifier)
        return state.artifacts.versions(owner,identifier)

    @router.get('/artifacts/{identifier}/export')
    def export(identifier: str,request: Request,format: str,version: int | None=None):
        owner,item=owned(request,identifier)
        data,mime=state.artifacts.export(owner,identifier,format,version)
        return Response(data,media_type=mime,headers={'Cache-Control':'no-store',
            'Content-Disposition':f'attachment; filename="artifact-{identifier}.{format if format.isalnum() else "bin"}"'})

    @router.post('/inputs')
    async def upload(request: Request,project_id: str | None=None,name: str='Attachment'):
        owner=actor(request,project_id,'agent-run')
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>LIMIT:
                raise HTTPException(413,'Input exceeds 100 MiB')
            chunks.append(chunk)
        mime=request.headers.get('content-type','application/octet-stream').split(';')[0]
        supported=mime.startswith(('image/','audio/','video/')) or mime in {'text/plain','text/markdown','text/csv','application/json','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
        if not supported:
            raise HTTPException(415,'Unsupported media type')
        blob=b''.join(chunks)
        if mime.startswith('image/'):
            try:
                from PIL import Image
                image=Image.open(io.BytesIO(blob));image.verify()
            except Exception as exc:
                raise HTTPException(400,'Invalid image input') from exc
        return state.artifacts.create(owner,project_id,'input',name,blob=blob,mime=mime)

    @router.post('/inputs/{identifier}/context')
    async def context(identifier: str,request: Request):
        owner,_=owned(request,identifier,'agent-run')
        try:
            return await asyncio.to_thread(input_context,state.artifacts,owner,identifier)
        except (ValueError,UnicodeError,subprocess.SubprocessError) as exc:
            raise HTTPException(400,'Could not convert input; inspect the file format and size') from exc

    @router.post('/generate')
    async def generate(body: GenerateInput,request: Request):
        owner=actor(request,body.project_id,'agent-run')
        return await state.media.generate(owner,body.project_id,body.model_dump())

    app.include_router(router)

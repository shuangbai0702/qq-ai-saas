"""One-shot QQ favorite sanitizer. No model or AstrBot runtime is loaded."""
import io,json,sys,warnings
from pathlib import Path
from PIL import Image,ImageSequence

def normalize(source,dest):
 raw=Path(source).read_bytes()
 if len(raw)>8*1024*1024:raise ValueError('input limit')
 with warnings.catch_warnings():
  warnings.simplefilter('error',Image.DecompressionBombWarning)
  with Image.open(io.BytesIO(raw)) as image:
   if image.format not in ('GIF','PNG','JPEG','WEBP') or image.width*image.height>4*1024*1024 or getattr(image,'n_frames',1)>300:raise ValueError('image limit')
   count=getattr(image,'n_frames',1)
   if image.width*image.height*count>80*1024*1024:raise ValueError('animation limit')
   frames=[];durations=[];indices=set(round(i*(count-1)/min(47,max(1,count-1))) for i in range(min(count,48)))
   for i,frame in enumerate(ImageSequence.Iterator(image)):
    duration=max(20,min(10000,int(frame.info.get('duration',100))))
    if i in indices:
     thumb=frame.convert('RGBA');thumb.thumbnail((256,256))
     if count>1:
      indexed=thumb.convert('RGB').quantize(colors=63);indexed.paste(255,mask=thumb.getchannel('A').point(lambda a:255 if a<128 else 0));indexed.info['transparency']=255;frames.append(indexed)
     else:frames.append(thumb)
     durations.append(duration)
    elif durations:durations[-1]+=duration
   output=io.BytesIO();animated=count>1
   if animated:frames[0].save(output,format='GIF',save_all=True,append_images=frames[1:],duration=durations,loop=0,transparency=255,background=255,disposal=2,optimize=False)
   else:frames[0].save(output,format='PNG',optimize=True)
   data=output.getvalue()
   if len(data)>1048576:raise ValueError('output limit')
   suffix='.gif' if animated else '.png';file=Path(str(dest)+suffix);file.write_bytes(data)
   return {'suffix':suffix,'mime':'image/gif' if animated else 'image/png','bytes':len(data),'animated':animated}

def preview(source,dest):
 if Path(source).stat().st_size>1048576:raise ValueError('preview input limit')
 with warnings.catch_warnings():
  warnings.simplefilter('error',Image.DecompressionBombWarning)
  with Image.open(source) as image:
   count=getattr(image,'n_frames',1)
   if image.width*image.height>4194304 or count>300:raise ValueError('preview limit')
   indices=sorted(set((0,count//2,count-1))) if count>1 else [0]
   sheet=Image.new('RGB',(192*len(indices),192),'white')
   for column,index in enumerate(indices):
    image.seek(index);frame=image.convert('RGBA');frame.thumbnail((192,192))
    sheet.paste(frame,(192*column,0),frame.getchannel('A'))
   from model_images import MAX_EDGE
   sheet.thumbnail((MAX_EDGE,MAX_EDGE),Image.Resampling.LANCZOS)
   sheet.save(dest,format='JPEG',quality=75)
   return {'sampledFrames':indices,'totalFrames':count,'audio':False}

if __name__=='__main__':
 try:print(json.dumps(preview(sys.argv[2],sys.argv[3]) if sys.argv[1]=='--preview' else normalize(sys.argv[1],sys.argv[2])))
 except Exception:print('Unsupported or oversized QQ favorite',file=sys.stderr);sys.exit(1)

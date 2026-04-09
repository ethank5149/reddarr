import re

with open('web/src/App.jsx', 'r') as f:
    content = f.read()

# Replace the wrapper div
# from: style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:200,backdropFilter:"blur(12px)"}}
# to: className="post-modal-container"

content = content.replace(
    'style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:200,backdropFilter:"blur(12px)"}}',
    'className="post-modal-container"'
)

# Replace the inner wrapper
# from: className="modal-enter" style={{background:"#1a2234",borderRadius:"20px 20px 0 0",width:"100%",maxWidth:"760px",maxHeight:"93vh",overflow:"auto",border:"1px solid #222",borderBottom:"none",boxShadow:"0 -8px 60px rgba(0,0,0,0.7)",paddingBottom:"env(safe-area-inset-bottom, 0)"}}
# to: className={`modal-enter post-modal-content ${(!selectedPost.is_video && !selectedPost.video_url && !selectedPost.url && !selectedPost.image_urls?.[0]) ? 'no-media' : ''}`}

content = content.replace(
    'className="modal-enter" style={{background:"#1a2234",borderRadius:"20px 20px 0 0",width:"100%",maxWidth:"760px",maxHeight:"93vh",overflow:"auto",border:"1px solid #222",borderBottom:"none",boxShadow:"0 -8px 60px rgba(0,0,0,0.7)",paddingBottom:"env(safe-area-inset-bottom, 0)"}}',
    'className={`modal-enter post-modal-content ${(!selectedPost.is_video && !selectedPost.video_url && !selectedPost.url && !selectedPost.image_urls?.[0]) ? \'no-media\' : \'\'}`}'
)

# Replace the top drag handle bar
# from: <div style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>
# to: <div className="mobile-only-drag-handle" style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>
content = content.replace(
    '<div style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>',
    '<div className="mobile-only-drag-handle" style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>'
)

# Replace video/image wrappers with .post-modal-media
# from: <div style={{background:"#000",position:"relative",overflow:"hidden"}}>
content = content.replace(
    '<div style={{background:"#000",position:"relative",overflow:"hidden"}}>',
    '<div className="post-modal-media">'
)

# from: <div style={{background:"#000",position:"relative",userSelect:"none"}}>
content = content.replace(
    '<div style={{background:"#000",position:"relative",userSelect:"none"}}>',
    '<div className="post-modal-media" style={{userSelect:"none"}}>'
)

# Replace the info container
# from: <div style={{padding:"20px 24px"}}>
content = content.replace(
    '<div style={{padding:"20px 24px"}}>',
    '<div className="post-modal-info" style={{padding:"20px 24px"}}>'
)

# Remove the inline max-height and width on video and img to let CSS handle it
content = content.replace(
    'style={{width:"100%",maxHeight:"480px",display:"block",background:"#000"}}',
    'style={{display:"block",background:"#000"}}'
)

content = content.replace(
    'style={{width:"100%",maxHeight:"460px",objectFit:"contain",display:"block"}}',
    'style={{display:"block"}}'
)

with open('web/src/App.jsx', 'w') as f:
    f.write(content)

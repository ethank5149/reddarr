import {useEffect,useState,useRef} from "react"
import axios from "axios"

export default function App(){
  const [posts,setPosts]=useState([])
  const [offset,setOffset]=useState(0)
  const [search,setSearch]=useState("")
  const [searchResults,setSearchResults]=useState(null)
  const [selectedPost,setSelectedPost]=useState(null)
  const [tagInput,setTagInput]=useState("")
  const [activeTab,setActiveTab]=useState("browse")
  const [adminData,setAdminData]=useState(null)
  const [logs,setLogs]=useState([])
  const [hoveredCard,setHoveredCard]=useState(null)
  const loader=useRef()
  const searchTimeout=useRef()

  useEffect(()=>{load()},[])

  useEffect(()=>{
    if(activeTab === "admin" && !adminData){
      loadAdmin()
    }
  },[activeTab])

  function load(){
   axios.get(`/api/posts?limit=50&offset=${offset}`)
    .then(r=>{
      const newPosts = r.data.map(p => ({
        id: p[0],
        title: p[1],
        url: p[2],
        selftext: p[3],
        subreddit: p[4],
        author: p[5]
      }))
      setPosts(prev=>[...prev,...newPosts])
      setOffset(o=>o+50)
    })
  }

  function loadAdmin(){
    axios.get("/api/admin/stats").then(r=>{
      setAdminData(r.data)
    })
    axios.get("/api/admin/logs?limit=20").then(r=>{
      setLogs(r.data)
    })
  }

  function toggleTarget(ttype, name){
    axios.post(`/api/admin/target/${ttype}/${name}/toggle`).then(()=>{
      loadAdmin()
    })
  }

  function rescanTarget(ttype, name){
    axios.post(`/api/admin/target/${ttype}/${name}/rescan`).then(()=>{
      loadAdmin()
    })
  }

  function formatEta(seconds){
    if(!seconds) return "N/A"
    if(seconds < 60) return `${Math.round(seconds)}s`
    if(seconds < 3600) return `${Math.round(seconds/60)}m`
    if(seconds < 86400) return `${Math.round(seconds/3600)}h`
    return `${Math.round(seconds/86400)}d`
  }

  function formatRate(rate){
    if(!rate) return "0/s"
    if(rate < 0.001) return `${(rate*60).toFixed(1)}/m`
    return `${rate.toFixed(3)}/s`
  }

  useEffect(()=>{
   const obs=new IntersectionObserver(entries=>{
     if(entries[0].isIntersecting && !searchResults) load()
   })
   if(loader.current) obs.observe(loader.current)
  },[loader.current, searchResults])

  function handleSearch(e){
    setSearch(e.target.value)
    clearTimeout(searchTimeout.current)
    if(!e.target.value.trim()){
      setSearchResults(null)
      return
    }
    searchTimeout.current = setTimeout(()=>{
      axios.get(`/api/search?q=${encodeURIComponent(e.target.value)}`)
        .then(r=>{
          setSearchResults(r.data.map(p=>({id:p[0], title:p[1]})))
        })
    },300)
  }

  function addTag(){
    if(!tagInput.trim() || !selectedPost) return
    axios.post(`/api/tag?post_id=${selectedPost.id}&tag=${encodeURIComponent(tagInput)}`)
      .then(()=>{
        setSelectedPost({...selectedPost, tags:[...(selectedPost.tags||[]),tagInput]})
        setTagInput("")
      })
  }

  function formatDate(ts){
    if(!ts) return ""
    return new Date(ts * 1000).toLocaleDateString()
  }

  function truncateText(text, len=150){
    if(!text) return ""
    return text.length > len ? text.substring(0, len) + "..." : text
  }

  return (
    <div style={{minHeight:"100vh",background:"#0d0d0d",color:"#fff",fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif"}}>
      <header style={{padding:"16px 24px",background:"linear-gradient(180deg,#1a1a1a 0%,#141414 100%)",borderBottom:"1px solid #222",position:"sticky",top:0,zIndex:100,backdropFilter:"blur(10px)"}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",gap:"24px"}}>
            <div style={{position:"relative"}}>
              <img src="/icon.png" alt="Logo" style={{width:"40px",height:"40px",borderRadius:"12px",boxShadow:"0 4px 12px rgba(255,69,0,0.3)"}} />
              <div style={{position:"absolute",bottom:"-2px",right:"-2px",width:"10px",height:"10px",background:"#46d160",borderRadius:"50%",border:"2px solid #141414"}} />
            </div>
            <h1 style={{margin:0,fontSize:"22px",fontWeight:"700",background:"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)","-webkit-background-clip":"text","-webkit-text-fill-color":"transparent",backgroundClip:"text"}}>Reddit Archive</h1>
            <div style={{display:"flex",gap:"4px",background:"#1a1a1a",padding:"4px",borderRadius:"10px"}}>
              {[
                {id:"browse",label:"Browse",icon:"⊞"},
                {id:"admin",label:"Admin",icon:"⚙"}
              ].map(tab=>(
                <button
                  key={tab.id}
                  onClick={()=>setActiveTab(tab.id)}
                  style={{
                    padding:"8px 16px",
                    background:activeTab===tab.id?"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)":"transparent",
                    border:"none",
                    borderRadius:"8px",
                    color:"#fff",
                    cursor:"pointer",
                    fontWeight:activeTab===tab.id?"600":"400",
                    fontSize:"14px",
                    display:"flex",
                    alignItems:"center",
                    gap:"6px",
                    transition:"all 0.2s ease"
                  }}
                >
                  <span style={{fontSize:"16px"}}>{tab.icon}</span>
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
          <div style={{position:"relative"}}>
            <span style={{position:"absolute",left:"14px",top:"50%",transform:"translateY(-50%)",color:"#666",fontSize:"16px"}}>⌕</span>
            <input 
              type="text" 
              placeholder="Search archived posts..." 
              value={search}
              onChange={handleSearch}
              style={{
                padding:"12px 16px 12px 42px",
                borderRadius:"24px",
                border:"1px solid #333",
                width:"320px",
                background:"#1a1a1a",
                color:"#fff",
                fontSize:"14px",
                outline:"none",
                transition:"all 0.2s ease",
                boxShadow:"0 2px 8px rgba(0,0,0,0.2)"
              }}
            />
          </div>
        </div>
      </header>

      {activeTab === "admin" && adminData && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"24px"}}>
            <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
            <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Overview</h2>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:"16px",marginBottom:"40px"}}>
            {[
              {label:"Total Posts",value:adminData.total_posts,color:"#ff4500",icon:"📄"},
              {label:"Comments",value:adminData.total_comments,color:"#7193ff",icon:"💬"},
              {label:"Media Downloaded",value:adminData.downloaded_media,color:"#46d160",icon:"⬇"},
              {label:"Media Pending",value:adminData.pending_media,color:"#f9c300",icon:"⏳"},
              {label:"Total Media",value:adminData.total_media,color:"#fff",icon:"📁"}
            ].map(s=>(
              <div key={s.label} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"16px",border:"1px solid #2a2a2a",boxShadow:"0 4px 20px rgba(0,0,0,0.3)"}}>
                <div style={{display:"flex",alignItems:"center",gap:"8px",fontSize:"12px",color:"#666",marginBottom:"8px",textTransform:"uppercase",letterSpacing:"0.5px"}}>
                  <span>{s.icon}</span>
                  {s.label}
                </div>
                <div style={{fontSize:"32px",fontWeight:"700",color:s.color,textShadow:"0 0 30px rgba(255,69,0,0.2)"}}>{s.value?.toLocaleString()}</div>
              </div>
            ))}
          </div>

          <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
            <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
            <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Scrape Targets</h2>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,360px)",gap:"16px",marginBottom:"40px"}}>
            {adminData.targets.map(t=>(
              <div key={`${t.type}-${t.name}`} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"16px",border:t.enabled?"1px solid #ff450044":"1px solid #2a2a2a",opacity:t.enabled?1:0.7,boxShadow:"0 4px 20px rgba(0,0,0,0.3)",transition:"all 0.2s ease"}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:"16px"}}>
                  <div>
                    <span style={{fontSize:"11px",color:"#666",textTransform:"uppercase",letterSpacing:"1px",display:"block",marginBottom:"4px"}}>{t.type}</span>
                    <div style={{fontSize:"20px",fontWeight:"700",color:"#fff"}}>{t.type==="subreddit"?"r/":"u/"}{t.name}</div>
                  </div>
                  <div style={{display:"flex",gap:"8px"}}>
                    <button onClick={()=>toggleTarget(t.type,t.name)} style={{padding:"8px 14px",background:t.enabled?"#46d160":"#3a3a3a",border:"none",borderRadius:"8px",color:t.enabled?"#000":"#888",cursor:"pointer",fontSize:"12px",fontWeight:"600",transition:"all 0.2s ease"}}>
                      {t.enabled?"Active":"Paused"}
                    </button>
                    <button onClick={()=>rescanTarget(t.type,t.name)} style={{padding:"8px 14px",background:"#ff4500",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"12px",fontWeight:"500",transition:"all 0.2s ease",boxShadow:"0 2px 8px rgba(255,69,0,0.3)"}}>
                      ↻ Rescan
                    </button>
                  </div>
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(2,1fr)",gap:"12px",fontSize:"13px",marginBottom:"16px"}}>
                  <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Posts</span><span style={{fontWeight:"600",color:"#fff"}}>{t.post_count?.toLocaleString()}</span></div>
                  <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Rate</span><span style={{fontWeight:"600",color:"#46d160"}}>{formatRate(t.rate_per_second)}</span></div>
                  <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Media</span><span style={{fontWeight:"600",color:"#fff"}}>{t.downloaded_media}/{t.total_media}</span></div>
                  <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>ETA</span><span style={{fontWeight:"600",color:"#f9c300"}}>{formatEta(t.eta_seconds)}</span></div>
                </div>
                <div style={{background:"#141414",height:"8px",borderRadius:"4px",overflow:"hidden"}}>
                  <div style={{width:`${Math.min(100,t.progress_percent)}%`,background:"linear-gradient(90deg,#ff4500,#ff6a33)",height:"100%",borderRadius:"4px",transition:"width 0.5s ease"}}/>
                </div>
                <div style={{fontSize:"11px",color:"#555",marginTop:"8px",textAlign:"right"}}>{t.progress_percent}% complete</div>
                {t.last_created && (
                  <div style={{fontSize:"11px",color:"#444",marginTop:"8px",display:"flex",alignItems:"center",gap:"4px"}}>
                    <span style={{width:"6px",height:"6px",background:"#46d160",borderRadius:"50%"}} />
                    Last scraped: {new Date(t.last_created).toLocaleString()}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
            <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
            <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Recent Activity</h2>
          </div>
          <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"16px",border:"1px solid #2a2a2a",overflow:"hidden",boxShadow:"0 4px 20px rgba(0,0,0,0.3)"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
              <thead>
                <tr style={{background:"#141414",borderBottom:"1px solid #2a2a2a"}}>
                  <th style={{padding:"14px 16px",textAlign:"left",color:"#666",fontWeight:"500",fontSize:"12px",textTransform:"uppercase",letterSpacing:"0.5px"}}>Time</th>
                  <th style={{padding:"14px 16px",textAlign:"left",color:"#666",fontWeight:"500",fontSize:"12px",textTransform:"uppercase",letterSpacing:"0.5px"}}>Subreddit</th>
                  <th style={{padding:"14px 16px",textAlign:"left",color:"#666",fontWeight:"500",fontSize:"12px",textTransform:"uppercase",letterSpacing:"0.5px"}}>Author</th>
                  <th style={{padding:"14px 16px",textAlign:"left",color:"#666",fontWeight:"500",fontSize:"12px",textTransform:"uppercase",letterSpacing:"0.5px"}}>Title</th>
                </tr>
              </thead>
              <tbody>
                {logs.map(l=>(
                  <tr key={l.id} style={{borderBottom:"1px solid #222",transition:"background 0.15s ease"}}>
                    <td style={{padding:"12px 16px",color:"#555"}}>{l.created_utc?new Date(l.created_utc).toLocaleTimeString():"-"}</td>
                    <td style={{padding:"12px 16px"}}><span style={{background:"#ff450022",color:"#ff4500",padding:"4px 8px",borderRadius:"4px",fontSize:"12px",fontWeight:"500"}}>{l.subreddit||"-"}</span></td>
                    <td style={{padding:"12px 16px",color:"#888"}}>{l.author||"-"}</td>
                    <td style={{padding:"12px 16px",maxWidth:"400px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#ccc"}}>{l.title||"-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "browse" && (
        <>
          {searchResults && (
            <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"24px"}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                  <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Search Results <span style={{color:"#666",fontWeight:"400"}}>({searchResults.length})</span></h2>
                </div>
                <button onClick={()=>{setSearchResults(null);setSearch("")}} style={{padding:"10px 20px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"14px",transition:"all 0.2s ease"}}>Clear Search</button>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"16px"}}>
                {searchResults.map(p=>(
                  <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"14px",cursor:"pointer",border:"1px solid #2a2a2a",transition:"all 0.2s ease",boxShadow:"0 4px 12px rgba(0,0,0,0.2)"}}>
                    <div style={{fontWeight:"500",marginBottom:"8px",lineHeight:"1.4",color:"#e0e0e0"}}>{p.title}</div>
                    <div style={{fontSize:"12px",color:"#555",marginTop:"8px"}}>ID: {p.id}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!searchResults && (
            <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"20px"}}>
                {posts.map((p,i)=>(
                  <div 
                    key={p.id} 
                    onClick={()=>setSelectedPost(p)} 
                    onMouseEnter={()=>setHoveredCard(p.id)}
                    onMouseLeave={()=>setHoveredCard(null)}
                    style={{
                      background:"linear-gradient(145deg,#1e1e1e,#171717)",
                      borderRadius:"16px",
                      overflow:"hidden",
                      cursor:"pointer",
                      transition:"all 0.25s ease",
                      transform:hoveredCard===p.id?"translateY(-4px)":"translateY(0)",
                      boxShadow:hoveredCard===p.id?"0 12px 40px rgba(255,69,0,0.15)":"0 4px 12px rgba(0,0,0,0.3)",
                      border:"1px solid #2a2a2a"
                    }}>
                    {p.url ? (
                      <div style={{aspectRatio:"1",background:"#141414",position:"relative",overflow:"hidden"}}>
                        <img src={p.url} style={{width:"100%",height:"100%",objectFit:"cover",transition:"transform 0.3s ease"}} onError={e=>e.target.style.display="none"}/>
                        <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
                          <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit || "reddit"}</div>
                        </div>
                      </div>
                    ) : (
                      <div style={{padding:"24px",background:"linear-gradient(135deg, #1a1a1a 0%, #222 100%)",minHeight:"180px",display:"flex",flexDirection:"column"}}>
                        <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"12px"}}>{p.subreddit || "reddit"}</div>
                        <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4",color:"#fff"}}>{p.title}</div>
                        {p.selftext && (
                          <div style={{fontSize:"13px",color:"#777",lineHeight:"1.6",flex:1}}>
                            {truncateText(p.selftext)}
                          </div>
                        )}
                      </div>
                    )}
                    {p.url && (
                      <div style={{padding:"16px"}}>
                        <div style={{fontSize:"11px",color:"#666",textTransform:"uppercase",letterSpacing:"1px",marginBottom:"6px"}}>{p.subreddit || "reddit"}</div>
                        <div style={{fontSize:"14px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#ccc"}}>{p.title}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div ref={loader} style={{padding:"60px",textAlign:"center",color:"#444",fontSize:"14px"}}>
                <div style={{display:"inline-flex",alignItems:"center",gap:"8px"}}>
                  <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#ff4500",borderRadius:"50%",animation:"spin 1s linear infinite"}} />
                  Loading more posts...
                </div>
                <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
              </div>
            </div>
          )}
        </>
      )}

      {selectedPost && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.9)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>setSelectedPost(null)}>
          <div style={{background:"#0d0d0d",borderRadius:"20px",maxWidth:"720px",width:"100%",maxHeight:"90vh",overflow:"auto",border:"1px solid #222",boxShadow:"0 24px 80px rgba(0,0,0,0.5)"}} onClick={e=>e.stopPropagation()}>
            {selectedPost.url && (
              <div style={{background:"#000",position:"relative"}}>
                <img src={selectedPost.url} style={{width:"100%",maxHeight:"450px",objectFit:"contain",borderRadius:"20px 20px 0 0"}} onError={e=>e.target.style.display="none"}/>
                <div style={{position:"absolute",top:"16px",right:"16px"}}>
                  <a href={selectedPost.url} target="_blank" rel="noopener" style={{background:"rgba(0,0,0,0.7)",color:"#fff",padding:"8px 14px",borderRadius:"8px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px"}}>↗ Open</a>
                </div>
              </div>
            )}
            <div style={{padding:"28px"}}>
              <div style={{display:"flex",gap:"16px",fontSize:"13px",color:"#666",marginBottom:"20px"}}>
                <span style={{color:"#ff4500",fontWeight:"600"}}>r/{selectedPost.subreddit || "reddit"}</span>
                <span>•</span>
                <span style={{color:"#888"}}>u/{selectedPost.author || "unknown"}</span>
                <span>•</span>
                <span style={{color:"#555"}}>ID: {selectedPost.id}</span>
              </div>
              <h2 style={{margin:"0 0 24px 0",fontSize:"24px",lineHeight:"1.4",fontWeight:"600",color:"#fff"}}>{selectedPost.title}</h2>
              {selectedPost.selftext && (
                <div style={{background:"linear-gradient(145deg,#141414,#1a1a1a)",padding:"24px",borderRadius:"14px",marginBottom:"24px",fontSize:"15px",lineHeight:"1.8",color:"#bbb",whiteSpace:"pre-wrap",border:"1px solid #222"}}>
                  {selectedPost.selftext}
                </div>
              )}
              <div style={{display:"flex",gap:"12px",alignItems:"center"}}>
                <input 
                  type="text" 
                  placeholder="Add a tag..." 
                  value={tagInput}
                  onChange={e=>setTagInput(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&addTag()}
                  style={{flex:1,padding:"14px 18px",borderRadius:"12px",border:"1px solid #333",background:"#141414",color:"#fff",fontSize:"14px",outline:"none",transition:"all 0.2s ease"}}
                />
                <button onClick={addTag} style={{padding:"14px 28px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"12px",color:"#fff",cursor:"pointer",fontWeight:"600",fontSize:"14px",boxShadow:"0 4px 15px rgba(255,69,0,0.3)",transition:"all 0.2s ease"}}>Add Tag</button>
              </div>
              {selectedPost.tags && selectedPost.tags.length > 0 && (
                <div style={{marginTop:"20px",display:"flex",gap:"8px",flexWrap:"wrap"}}>
                  {selectedPost.tags.map(t=>(
                    <span key={t} style={{background:"#ff450022",color:"#ff4500",padding:"6px 12px",borderRadius:"20px",fontSize:"12px",fontWeight:"500"}}>{t}</span>
                  ))}
                </div>
              )}
            </div>
            <div style={{padding:"16px 28px",borderTop:"1px solid #1a1a1a",display:"flex",justifyContent:"flex-end"}}>
              <button onClick={()=>setSelectedPost(null)} style={{padding:"10px 20px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"13px",transition:"all 0.2s ease"}}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

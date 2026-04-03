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

  function resetTarget(ttype, name){
    if(!confirm(`Reset progress for ${ttype}/${name}? This will restart from beginning.`)) return
    axios.post(`/api/admin/target/${ttype}/${name}/reset`).then(()=>{
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
    <div style={{minHeight:"100vh",background:"#1a1a1a",color:"#fff",fontFamily:"system-ui, sans-serif"}}>
      <header style={{padding:"15px 20px",background:"#2a2a2a",borderBottom:"1px solid #333",position:"sticky",top:0,zIndex:100}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",gap:"30px"}}>
            <h1 style={{margin:0,fontSize:"24px",color:"#ff4500"}}>Reddit Archive</h1>
            <div style={{display:"flex",gap:"5px"}}>
              {[
                {id:"browse",label:"Browse"},
                {id:"admin",label:"Admin Dashboard"}
              ].map(tab=>(
                <button
                  key={tab.id}
                  onClick={()=>setActiveTab(tab.id)}
                  style={{
                    padding:"8px 16px",
                    background:activeTab===tab.id?"#ff4500":"transparent",
                    border:"none",
                    borderRadius:"5px",
                    color:"#fff",
                    cursor:"pointer",
                    fontWeight:activeTab===tab.id?"600":"400"
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
          <input 
            type="text" 
            placeholder="Search posts..." 
            value={search}
            onChange={handleSearch}
            style={{
              padding:"10px 15px",
              borderRadius:"20px",
              border:"none",
              width:"300px",
              background:"#3a3a3a",
              color:"#fff"
            }}
          />
        </div>
      </header>

      {activeTab === "admin" && adminData && (
        <div style={{padding:"20px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:"15px",marginBottom:"30px"}}>
            {[
              {label:"Total Posts",value:adminData.total_posts,color:"#ff4500"},
              {label:"Comments",value:adminData.total_comments,color:"#7193ff"},
              {label:"Media Downloaded",value:adminData.downloaded_media,color:"#46d160"},
              {label:"Total Media",value:adminData.total_media,color:"#f9c300"}
            ].map(s=>(
              <div key={s.label} style={{background:"#2a2a2a",padding:"20px",borderRadius:"10px",border:"1px solid #333"}}>
                <div style={{fontSize:"13px",color:"#888",marginBottom:"5px"}}>{s.label}</div>
                <div style={{fontSize:"28px",fontWeight:"700",color:s.color}}>{s.value?.toLocaleString()}</div>
              </div>
            ))}
          </div>

          <h2 style={{marginBottom:"15px"}}>Scrape Targets</h2>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,350px)",gap:"15px",marginBottom:"30px"}}>
            {adminData.targets.map(t=>(
              <div key={`${t.type}-${t.name}`} style={{background:"#2a2a2a",padding:"15px",borderRadius:"10px",border:t.enabled?"1px solid #ff4500":"1px solid #333",opacity:t.enabled?1:0.6}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"10px"}}>
                  <div>
                    <span style={{fontSize:"11px",color:"#888",textTransform:"uppercase"}}>{t.type}</span>
                    <div style={{fontSize:"18px",fontWeight:"600"}}>{t.type==="subreddit"?"r/":"u/"}{t.name}</div>
                  </div>
                  <div style={{display:"flex",gap:"5px"}}>
                    <button onClick={()=>toggleTarget(t.type,t.name)} style={{padding:"5px 10px",background:t.enabled?"#46d160":"#f9c300",border:"none",borderRadius:"4px",color:"#000",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>
                      {t.enabled?"Active":"Paused"}
                    </button>
                    <button onClick={()=>resetTarget(t.type,t.name)} style={{padding:"5px 10px",background:"#ff4500",border:"none",borderRadius:"4px",color:"#fff",cursor:"pointer",fontSize:"12px"}}>
                      Reset
                    </button>
                  </div>
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(2,1fr)",gap:"10px",fontSize:"13px"}}>
                  <div><span style={{color:"#888"}}>Posts:</span> <span style={{fontWeight:"600"}}>{t.post_count}</span></div>
                  <div><span style={{color:"#888"}}>Rate:</span> <span style={{fontWeight:"600"}}>{formatRate(t.rate_per_second)}</span></div>
                  <div><span style={{color:"#888"}}>Media:</span> <span style={{fontWeight:"600"}}>{t.downloaded_media}/{t.total_media}</span></div>
                  <div><span style={{color:"#888"}}>ETA:</span> <span style={{fontWeight:"600",color:"#46d160"}}>{formatEta(t.eta_seconds)}</span></div>
                </div>
                <div style={{marginTop:"10px"}}>
                  <div style={{background:"#1a1a1a",height:"6px",borderRadius:"3px",overflow:"hidden"}}>
                    <div style={{width:`${Math.min(100,t.progress_percent)}%`,background:"#ff4500",height:"100%"}}/>
                  </div>
                  <div style={{fontSize:"11px",color:"#666",marginTop:"4px",textAlign:"right"}}>{t.progress_percent}% (est. 1000 posts)</div>
                </div>
                {t.last_created && (
                  <div style={{fontSize:"11px",color:"#666",marginTop:"8px"}}>
                    Last: {new Date(t.last_created).toLocaleString()}
                  </div>
                )}
              </div>
            ))}
          </div>

          <h2 style={{marginBottom:"15px"}}>Recent Activity</h2>
          <div style={{background:"#2a2a2a",borderRadius:"10px",border:"1px solid #333",overflow:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
              <thead>
                <tr style={{background:"#252525",borderBottom:"1px solid #333"}}>
                  <th style={{padding:"12px",textAlign:"left",color:"#888"}}>Time</th>
                  <th style={{padding:"12px",textAlign:"left",color:"#888"}}>Subreddit</th>
                  <th style={{padding:"12px",textAlign:"left",color:"#888"}}>Author</th>
                  <th style={{padding:"12px",textAlign:"left",color:"#888"}}>Title</th>
                </tr>
              </thead>
              <tbody>
                {logs.map(l=>(
                  <tr key={l.id} style={{borderBottom:"1px solid #333"}}>
                    <td style={{padding:"10px 12px",color:"#888"}}>{l.created_utc?new Date(l.created_utc).toLocaleTimeString():"-"}</td>
                    <td style={{padding:"10px 12px",color:"#ff4500"}}>{l.subreddit||"-"}</td>
                    <td style={{padding:"10px 12px"}}>{l.author||"-"}</td>
                    <td style={{padding:"10px 12px",maxWidth:"400px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{l.title||"-"}</td>
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
            <div style={{padding:"20px",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"20px"}}>
                <h2>Search Results ({searchResults.length})</h2>
                <button onClick={()=>{setSearchResults(null);setSearch("")}} style={{padding:"8px 16px",background:"#ff4500",border:"none",borderRadius:"5px",color:"#fff",cursor:"pointer"}}>Clear</button>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,280px)",gap:"15px"}}>
                {searchResults.map(p=>(
                  <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"#2a2a2a",padding:"15px",borderRadius:"8px",cursor:"pointer",border:"1px solid #333"}}>
                    <div style={{fontWeight:"500",marginBottom:"8px"}}>{p.title}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!searchResults && (
            <div style={{padding:"20px",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"20px"}}>
                {posts.map(p=>(
                  <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"#2a2a2a",borderRadius:"12px",overflow:"hidden",cursor:"pointer",transition:"transform 0.2s, box-shadow 0.2s",border:"1px solid #333"}}>
                    {p.url ? (
                      <div style={{aspectRatio:"1",background:"#3a3a3a"}}>
                        <img src={p.url} style={{width:"100%",height:"100%",objectFit:"cover"}} onError={e=>e.target.style.display="none"}/>
                      </div>
                    ) : (
                      <div style={{padding:"20px",background:"linear-gradient(135deg, #2a2a2a 0%, #333 100%)"}}>
                        <div style={{fontSize:"12px",color:"#888",marginBottom:"10px",textTransform:"uppercase",letterSpacing:"1px"}}>
                          {p.subreddit || "reddit"}
                        </div>
                        <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4"}}>{p.title}</div>
                        {p.selftext && (
                          <div style={{fontSize:"14px",color:"#aaa",lineHeight:"1.6"}}>
                            {truncateText(p.selftext)}
                          </div>
                        )}
                      </div>
                    )}
                    {p.url && (
                      <div style={{padding:"12px"}}>
                        <div style={{fontSize:"12px",color:"#888",marginBottom:"5px",textTransform:"uppercase",letterSpacing:"1px"}}>
                          {p.subreddit || "reddit"}
                        </div>
                        <div style={{fontSize:"14px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.title}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div ref={loader} style={{padding:"40px",textAlign:"center",color:"#666"}}>Loading more...</div>
            </div>
          )}
        </>
      )}

      {selectedPost && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.85)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px"}} onClick={()=>setSelectedPost(null)}>
          <div style={{background:"#1a1a1a",borderRadius:"16px",maxWidth:"700px",width:"100%",maxHeight:"85vh",overflow:"auto",border:"1px solid #333"}} onClick={e=>e.stopPropagation()}>
            {selectedPost.url && (
              <div style={{background:"#000"}}>
                <img src={selectedPost.url} style={{width:"100%",maxHeight:"400px",objectFit:"contain"}} onError={e=>e.target.style.display="none"}/>
              </div>
            )}
            <div style={{padding:"25px"}}>
              <div style={{display:"flex",gap:"15px",fontSize:"13px",color:"#888",marginBottom:"15px"}}>
                <span style={{color:"#ff4500"}}>r/{selectedPost.subreddit || "reddit"}</span>
                <span>u/{selectedPost.author || "unknown"}</span>
              </div>
              <h2 style={{margin:"0 0 20px 0",fontSize:"22px",lineHeight:"1.4"}}>{selectedPost.title}</h2>
              {selectedPost.selftext && (
                <div style={{background:"#252525",padding:"20px",borderRadius:"10px",marginBottom:"20px",fontSize:"15px",lineHeight:"1.7",color:"#ccc",whiteSpace:"pre-wrap"}}>
                  {selectedPost.selftext}
                </div>
              )}
              <div style={{display:"flex",gap:"10px"}}>
                <input 
                  type="text" 
                  placeholder="Add tag..." 
                  value={tagInput}
                  onChange={e=>setTagInput(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&addTag()}
                  style={{flex:1,padding:"12px",borderRadius:"8px",border:"1px solid #333",background:"#252525",color:"#fff",fontSize:"14px"}}
                />
                <button onClick={addTag} style={{padding:"12px 24px",background:"#ff4500",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontWeight:"600"}}>Add Tag</button>
              </div>
              <div style={{marginTop:"20px",color:"#555",fontSize:"12px"}}>Post ID: {selectedPost.id}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import {useEffect,useState,useRef,useCallback} from "react"
import axios from "axios"

export default function App(){
  const [posts,setPosts]=useState([])
  const [search,setSearch]=useState("")
  const [searchResults,setSearchResults]=useState(null)
  const [selectedPost,setSelectedPost]=useState(null)
  const [tagInput,setTagInput]=useState("")
  const [activeTab,setActiveTab]=useState("browse")
  const [adminData,setAdminData]=useState(null)
  const [logs,setLogs]=useState([])
  const [hoveredCard,setHoveredCard]=useState(null)
  const [adminLoading, setAdminLoading] = useState(false)
  const [queueInfo, setQueueInfo] = useState(null)
  const [healthStatus, setHealthStatus] = useState(null)
  const [newPostsAvailable, setNewPostsAvailable] = useState(0)
  const [liveConnected, setLiveConnected] = useState(false)
  const [resetModal, setResetModal] = useState(false)
  const [resetInput, setResetInput] = useState("")
  const [resetLoading, setResetLoading] = useState(false)
  const [resetResult, setResetResult] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [highlightedRows, setHighlightedRows] = useState(new Set())
  const [addTargetType, setAddTargetType] = useState("subreddit")
  const [addTargetName, setAddTargetName] = useState("")

  // Filter + sort state
  const [filterSubreddit, setFilterSubreddit] = useState("")
  const [filterAuthor, setFilterAuthor] = useState("")
  const [filterMediaType, setFilterMediaType] = useState("all") // all | image | video | text
  const [sortBy, setSortBy] = useState("newest") // newest | oldest | title_asc | title_desc

  // Refs to avoid stale closures in async callbacks
  const offsetRef = useRef(0)
  const filtersRef = useRef({ subreddit:"", author:"", mediaType:"all", sort:"newest" })

  const loader=useRef()
  const searchTimeout=useRef()
  const esRef=useRef(null)
  const highlightTimerRef=useRef(null)

  // SSE real-time connection
  useEffect(()=>{
    function connect(){
      if(esRef.current) esRef.current.close()
      const es = new EventSource("/api/events")
      esRef.current = es

      es.onopen = () => setLiveConnected(true)

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if(data.error) return

          setLastUpdated(new Date())

          setAdminData(prev => ({
            ...(prev || {}),
            total_posts: data.total_posts ?? prev?.total_posts,
            total_comments: data.total_comments ?? prev?.total_comments,
            downloaded_media: data.downloaded_media ?? prev?.downloaded_media,
            pending_media: data.pending_media ?? prev?.pending_media,
            total_media: data.total_media ?? prev?.total_media,
            targets: data.targets ?? prev?.targets,
          }))

          if(data.health) setHealthStatus(data.health)

          setQueueInfo(prev => ({
            ...(prev||{}),
            queue_length: data.queue_length ?? (prev?.queue_length ?? 0)
          }))

          if(data.new_posts && data.new_posts.length > 0){
            setNewPostsAvailable(n => n + data.new_posts.length)
            const newIds = new Set(data.new_posts.map(p => p.id))
            setHighlightedRows(newIds)
            if(highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
            highlightTimerRef.current = setTimeout(() => setHighlightedRows(new Set()), 4000)
            setLogs(prev => [
              ...data.new_posts.map(p=>({
                id: p.id,
                subreddit: p.subreddit,
                author: p.author,
                created_utc: p.created_utc,
                title: p.title
              })),
              ...prev
            ].slice(0,50))
          }
        } catch(err){
          console.error("SSE parse error:", err)
        }
      }

      es.onerror = () => {
        setLiveConnected(false)
        es.close()
        esRef.current = null
        setTimeout(connect, 5000)
      }
    }

    connect()
    return () => {
      if(esRef.current) esRef.current.close()
    }
  },[])

  useEffect(()=>{
    load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[])

  useEffect(()=>{
    if(activeTab === "admin" && !adminData){
      loadAdmin()
    }
  },[activeTab])

  // Polling fallback every 10s on admin tab
  useEffect(()=>{
    if(activeTab !== "admin") return
    const poll = setInterval(()=>{
      axios.get("/api/admin/stats").then(r=>{
        if(r.data) setAdminData(r.data)
      }).catch(()=>{})
      axios.get("/api/admin/queue").then(r=>{
        if(r.data) setQueueInfo(r.data)
      }).catch(()=>{})
      axios.get("/api/admin/health").then(r=>{
        if(r.data) setHealthStatus(r.data)
      }).catch(()=>{})
      setLastUpdated(new Date())
    }, 10000)
    return ()=> clearInterval(poll)
  },[activeTab])

  // Fetch full post detail (tags + comments) when modal opens
  useEffect(()=>{
    if(!selectedPost?.id) return
    axios.get(`/api/post/${selectedPost.id}`)
      .then(r=>{
        if(!r.data) return
        setSelectedPost(prev => prev?.id === r.data.id ? {
          ...prev,
          tags: r.data.tags || [],
          comments: r.data.comments || [],
          created_utc: r.data.created_utc || prev?.created_utc,
          video_url: r.data.video_url ?? prev?.video_url,
          is_video: r.data.is_video ?? prev?.is_video,
          url: r.data.image_url ?? prev?.url,
        } : prev)
      })
      .catch(()=>{})
  },[selectedPost?.id])

  function buildPostsQuery(offset){
    const f = filtersRef.current
    const params = new URLSearchParams({ limit:"50", offset:String(offset) })
    if(f.subreddit) params.set("subreddit", f.subreddit)
    if(f.author) params.set("author", f.author)
    if(f.mediaType === "image") params.set("media_type", "image")
    else if(f.mediaType === "video") params.set("media_type", "video")
    else if(f.mediaType === "text") params.set("media_type", "text")
    if(f.sort === "oldest"){ params.set("sort_by","created_utc"); params.set("sort_order","asc") }
    else if(f.sort === "title_asc"){ params.set("sort_by","title"); params.set("sort_order","asc") }
    else if(f.sort === "title_desc"){ params.set("sort_by","title"); params.set("sort_order","desc") }
    else { params.set("sort_by","created_utc"); params.set("sort_order","desc") }
    return `/api/posts?${params.toString()}`
  }

  function mapPost(p){
    return { id:p.id, title:p.title, url:p.image_url, video_url:p.video_url, is_video:p.is_video, selftext:p.selftext, subreddit:p.subreddit, author:p.author, created_utc:p.created_utc }
  }

  function load(){
    const currentOffset = offsetRef.current
    axios.get(buildPostsQuery(currentOffset))
    .then(r=>{
      const newPosts = r.data.map(mapPost)
      setPosts(prev=>[...prev,...newPosts])
      offsetRef.current = currentOffset + 50
    }).catch(err=>{
      console.error("Failed to load posts:", err)
    })
  }

  function refreshPosts(){
    offsetRef.current = 0
    axios.get(buildPostsQuery(0))
    .then(r=>{
      const newPosts = r.data.map(mapPost)
      setPosts(newPosts)
      offsetRef.current = 50
      setNewPostsAvailable(0)
    }).catch(err=>{
      console.error("Failed to refresh posts:", err)
    })
  }

  function applyFilters(newFilters){
    filtersRef.current = newFilters
    offsetRef.current = 0
    setPosts([])
    axios.get(buildPostsQuery(0))
    .then(r=>{
      setPosts(r.data.map(mapPost))
      offsetRef.current = 50
    }).catch(err=>{
      console.error("Failed to load posts:", err)
    })
  }

  function hasActiveFilters(){
    const f = filtersRef.current
    return f.subreddit || f.author || f.mediaType !== "all" || f.sort !== "newest"
  }

  function clearFilters(){
    const defaultFilters = { subreddit:"", author:"", mediaType:"all", sort:"newest" }
    setFilterSubreddit("")
    setFilterAuthor("")
    setFilterMediaType("all")
    setSortBy("newest")
    applyFilters(defaultFilters)
  }

  function loadAdmin(){
    setAdminLoading(true)
    Promise.all([
      axios.get("/api/admin/stats").catch(()=>({data:null})),
      axios.get("/api/admin/logs?limit=50").catch(()=>({data:[]})),
      axios.get("/api/admin/queue").catch(()=>({data:null})),
      axios.get("/api/admin/health").catch(()=>({data:null}))
    ]).then(([statsRes,logsRes,queueRes,healthRes])=>{
      if(statsRes.data) setAdminData(statsRes.data)
      if(logsRes.data) setLogs(logsRes.data)
      if(queueRes.data) setQueueInfo(queueRes.data)
      if(healthRes.data) setHealthStatus(healthRes.data)
      setAdminLoading(false)
    }).catch(()=>setAdminLoading(false))
  }

  function toggleTarget(ttype,name){
    axios.post(`/api/admin/target/${ttype}/${name}/toggle`).then(()=>loadAdmin()).catch(()=>alert("Failed to toggle target"))
  }

  function rescanTarget(ttype,name){
    axios.post(`/api/admin/target/${ttype}/${name}/rescan`).then(()=>loadAdmin()).catch(()=>alert("Failed to rescan target"))
  }

  function deleteTarget(ttype,name){
    if(!confirm(`Delete target ${ttype}:${name}? This removes it from the scrape list but does not delete archived posts.`)) return
    axios.delete(`/api/admin/target/${ttype}/${name}`).then(()=>loadAdmin()).catch(()=>alert("Failed to delete target"))
  }

  function addTarget(){
    const name = addTargetName.trim()
    if(!name) return
    axios.post(`/api/admin/target/${addTargetType}?name=${encodeURIComponent(name)}`)
      .then(()=>{ setAddTargetName(""); loadAdmin() })
      .catch(()=>alert("Failed to add target"))
  }

  function clearQueue(){
    if(!confirm("Clear the entire download queue?")) return
    axios.delete("/api/admin/queue").then(()=>loadAdmin()).catch(()=>alert("Failed to clear queue"))
  }

  function doReset(){
    if(resetInput !== "RESET") return
    setResetLoading(true)
    axios.delete("/api/admin/reset?confirm=RESET")
      .then(r=>{
        setResetResult(r.data)
        setResetLoading(false)
        setPosts([]); setOffset(0); setNewPostsAvailable(0); setLogs([])
        loadAdmin()
      })
      .catch(err=>{
        setResetResult({error: err.response?.data?.detail || err.message})
        setResetLoading(false)
      })
  }

  function removeTag(tag){
    if(!selectedPost) return
    axios.delete(`/api/post/${selectedPost.id}/tag/${encodeURIComponent(tag)}`)
      .then(()=>{
        setSelectedPost(prev=>prev ? {...prev, tags:(prev.tags||[]).filter(t=>t!==tag)} : prev)
      })
      .catch(()=>alert("Failed to remove tag"))
  }

  function addTag(){
    if(!tagInput.trim() || !selectedPost) return
    axios.post(`/api/tag?post_id=${selectedPost.id}&tag=${encodeURIComponent(tagInput)}`)
      .then(()=>{
        setSelectedPost(prev=>prev ? {...prev, tags:[...(prev.tags||[]),tagInput]} : prev)
        setTagInput("")
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
    if(!rate) return "—"
    const perDay = rate * 86400
    if(perDay < 1) return `${(perDay*7).toFixed(1)}/wk`
    return `${perDay.toFixed(1)}/day`
  }

  function truncateText(text,len=150){
    if(!text) return ""
    return text.length>len ? text.substring(0,len)+"..." : text
  }

  function formatTime(iso){
    if(!iso) return ""
    try{ return new Date(iso).toLocaleString() }catch{ return iso }
  }

  // Infinite scroll with cleanup
  useEffect(()=>{
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !searchResults) load()
    })
    if(loader.current) obs.observe(loader.current)
    return ()=> obs.disconnect()
  // load() uses refs so we only need to re-subscribe when loader/searchResults change
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
          setSearchResults(r.data.map(p=>({
            id: p.id,
            title: p.title,
            subreddit: p.subreddit,
            author: p.author,
            created_utc: p.created_utc,
          })))
        })
    },300)
  }

  const LiveDot = ({connected}) => (
    <div style={{display:"flex",alignItems:"center",gap:"6px",fontSize:"11px",color:connected?"#46d160":"#666"}}>
      <div style={{
        width:"8px",height:"8px",borderRadius:"50%",
        background:connected?"#46d160":"#444",
        boxShadow:connected?"0 0 6px #46d160":"none",
        animation:connected?"pulse 2s ease-in-out infinite":"none"
      }}/>
      {connected?"LIVE":"connecting..."}
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
    </div>
  )

  // Mini bar chart for posts_per_day
  function PostsChart({data}){
    if(!data || data.length === 0) return null
    const max = Math.max(...data.map(d=>d.count), 1)
    return (
      <div style={{marginBottom:"40px"}}>
        <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
          <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
          <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Posts (Last 7 Days)</h2>
        </div>
        <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"16px",border:"1px solid #2a2a2a",padding:"20px"}}>
          <div style={{display:"flex",alignItems:"flex-end",gap:"8px",height:"80px"}}>
            {data.map(d=>(
              <div key={d.date} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:"4px"}}>
                <span style={{fontSize:"10px",color:"#555",fontVariantNumeric:"tabular-nums"}}>{d.count}</span>
                <div style={{width:"100%",height:`${Math.round((d.count/max)*60)+4}px`,background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"3px 3px 0 0",minHeight:"4px"}}/>
                <span style={{fontSize:"9px",color:"#444",whiteSpace:"nowrap"}}>{d.date.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
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
            <h1 style={{margin:0,fontSize:"22px",fontWeight:"700",background:"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",backgroundClip:"text"}}>Reddit Archive</h1>
            <div style={{display:"flex",gap:"4px",background:"#1a1a1a",padding:"4px",borderRadius:"10px"}}>
              {[{id:"browse",label:"Browse",icon:"⊞"},{id:"admin",label:"Admin",icon:"⚙"}].map(tab=>(
                <button key={tab.id} onClick={()=>setActiveTab(tab.id)} style={{padding:"8px 16px",background:activeTab===tab.id?"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)":"transparent",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontWeight:activeTab===tab.id?"600":"400",fontSize:"14px",display:"flex",alignItems:"center",gap:"6px",transition:"all 0.2s ease"}}>
                  <span style={{fontSize:"16px"}}>{tab.icon}</span>{tab.label}
                </button>
              ))}
            </div>
            <LiveDot connected={liveConnected}/>
          </div>
          <div style={{display:"flex",alignItems:"center",gap:"16px"}}>
            {queueInfo && (
              <div style={{fontSize:"12px",color:"#555",display:"flex",alignItems:"center",gap:"6px"}}>
                <span style={{color:"#333"}}>queue:</span>
                <span style={{color:queueInfo.queue_length>0?"#f9c300":"#46d160",fontWeight:"600",fontVariantNumeric:"tabular-nums"}}>{(queueInfo.queue_length||0).toLocaleString()}</span>
              </div>
            )}
            <div style={{position:"relative"}}>
              <span style={{position:"absolute",left:"14px",top:"50%",transform:"translateY(-50%)",color:"#666",fontSize:"16px"}}>⌕</span>
              <input type="text" placeholder="Search archived posts..." value={search} onChange={handleSearch}
                style={{padding:"12px 16px 12px 42px",borderRadius:"24px",border:"1px solid #333",width:"320px",background:"#1a1a1a",color:"#fff",fontSize:"14px",outline:"none",transition:"all 0.2s ease",boxShadow:"0 2px 8px rgba(0,0,0,0.2)"}}/>
            </div>
          </div>
        </div>
      </header>

      {/* ── ADMIN TAB ── */}
      {activeTab === "admin" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          {adminLoading && <div style={{textAlign:"center",padding:"40px",color:"#666"}}>Loading admin data...</div>}
          {!adminLoading && !adminData && <div style={{textAlign:"center",padding:"40px",color:"#ff4500"}}>Failed to load admin data.</div>}

          {adminData && (<>
            {/* Header row */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>System Status</h2>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                {lastUpdated && <span style={{fontSize:"11px",color:"#444",fontVariantNumeric:"tabular-nums"}}>synced {lastUpdated.toLocaleTimeString()}</span>}
                <button onClick={loadAdmin} style={{padding:"8px 16px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"13px"}}>↻ Refresh</button>
                <button onClick={()=>{setResetModal(true);setResetInput("");setResetResult(null)}} style={{padding:"8px 16px",background:"#1a0000",border:"1px solid #550000",borderRadius:"8px",color:"#ff4444",cursor:"pointer",fontSize:"13px",fontWeight:"600"}}>⚠ Reset All Data</button>
              </div>
            </div>

            {/* Health + Queue */}
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,200px)",gap:"12px",marginBottom:"32px"}}>
              <div style={{background:"#1e1e1e",padding:"16px",borderRadius:"12px",border:"1px solid #2a2a2a"}}>
                <div style={{fontSize:"11px",color:"#666",marginBottom:"8px"}}>Health</div>
                <div style={{fontSize:"18px",fontWeight:"600",color:healthStatus?.status==="healthy"?"#46d160":healthStatus?.status==="degraded"?"#f9c300":"#ff4500"}}>{healthStatus?.status||"unknown"}</div>
                {healthStatus?.issues?.length>0 && <div style={{fontSize:"10px",color:"#ff4500",marginTop:"4px"}}>{healthStatus.issues.join(", ")}</div>}
              </div>
              <div style={{background:"#1e1e1e",padding:"16px",borderRadius:"12px",border:"1px solid #2a2a2a"}}>
                <div style={{fontSize:"11px",color:"#666",marginBottom:"8px",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                  <span>Queue</span>
                  {queueInfo?.queue_length > 0 && (
                    <button onClick={clearQueue} style={{fontSize:"10px",padding:"2px 6px",background:"#2a0000",border:"1px solid #550000",borderRadius:"4px",color:"#ff4444",cursor:"pointer"}}>clear</button>
                  )}
                </div>
                <div style={{fontSize:"18px",fontWeight:"600",color:queueInfo?.queue_length>0?"#f9c300":"#fff",transition:"color 0.3s"}}>{(queueInfo?.queue_length||0).toLocaleString()}</div>
                <div style={{fontSize:"10px",color:"#555",marginTop:"4px"}}>pending items</div>
              </div>
            </div>

            {/* Overview counts */}
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
                {label:"Total Media",value:adminData.total_media,color:"#fff",icon:"📁"},
              ].map(s=>(
                <div key={s.label} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"16px",border:"1px solid #2a2a2a",boxShadow:"0 4px 20px rgba(0,0,0,0.3)"}}>
                  <div style={{display:"flex",alignItems:"center",gap:"8px",fontSize:"12px",color:"#666",marginBottom:"8px",textTransform:"uppercase",letterSpacing:"0.5px"}}><span>{s.icon}</span>{s.label}</div>
                  <div style={{fontSize:"32px",fontWeight:"700",color:s.color,transition:"color 0.3s",fontVariantNumeric:"tabular-nums"}}>{s.value?.toLocaleString()}</div>
                </div>
              ))}
            </div>

            {/* Posts per day chart */}
            <PostsChart data={adminData.posts_per_day}/>
          </>)}

          {adminData && (<>
            {/* Scrape Targets */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:"12px",marginBottom:"16px",flexWrap:"wrap"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Scrape Targets</h2>
              </div>
              {/* Add target form */}
              <div style={{display:"flex",gap:"8px",alignItems:"center"}}>
                <select value={addTargetType} onChange={e=>setAddTargetType(e.target.value)}
                  style={{padding:"8px 10px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#ccc",fontSize:"13px",cursor:"pointer"}}>
                  <option value="subreddit">r/ subreddit</option>
                  <option value="user">u/ user</option>
                </select>
                <input type="text" placeholder="name" value={addTargetName} onChange={e=>setAddTargetName(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&addTarget()}
                  style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"160px"}}/>
                <button onClick={addTarget} disabled={!addTargetName.trim()}
                  style={{padding:"8px 16px",background:addTargetName.trim()?"linear-gradient(135deg,#ff4500,#ff6a33)":"#2a2a2a",border:"none",borderRadius:"8px",color:addTargetName.trim()?"#fff":"#555",cursor:addTargetName.trim()?"pointer":"not-allowed",fontSize:"13px",fontWeight:"600"}}>
                  + Add
                </button>
              </div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,360px)",gap:"16px",marginBottom:"40px"}}>
              {adminData.targets && adminData.targets.map(t=>(
                <div key={`${t.type}-${t.name}`} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"16px",border:t.enabled?"1px solid #ff450044":"1px solid #2a2a2a",opacity:t.enabled?1:0.7,boxShadow:"0 4px 20px rgba(0,0,0,0.3)",transition:"all 0.2s ease"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:"16px"}}>
                    <div>
                      <span style={{fontSize:"11px",color:"#666",textTransform:"uppercase",letterSpacing:"1px",display:"block",marginBottom:"4px"}}>{t.type}</span>
                      <div style={{fontSize:"20px",fontWeight:"700",color:"#fff"}}>{t.type==="subreddit"?"r/":"u/"}{t.name}</div>
                    </div>
                    <div style={{display:"flex",gap:"6px",flexWrap:"wrap",justifyContent:"flex-end"}}>
                      <button onClick={()=>toggleTarget(t.type,t.name)} style={{padding:"6px 12px",background:t.enabled?"#46d160":"#3a3a3a",border:"none",borderRadius:"8px",color:t.enabled?"#000":"#888",cursor:"pointer",fontSize:"12px",fontWeight:"600",transition:"all 0.2s ease"}}>
                        {t.enabled?"Active":"Paused"}
                      </button>
                      <button onClick={()=>rescanTarget(t.type,t.name)} style={{padding:"6px 12px",background:"#ff4500",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"12px",fontWeight:"500",transition:"all 0.2s ease"}}>
                        ↻ Rescan
                      </button>
                      <button onClick={()=>deleteTarget(t.type,t.name)} title="Remove target" style={{padding:"6px 10px",background:"#2a0000",border:"1px solid #550000",borderRadius:"8px",color:"#ff4444",cursor:"pointer",fontSize:"12px",transition:"all 0.2s ease"}}>
                        ✕
                      </button>
                    </div>
                  </div>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(2,1fr)",gap:"12px",fontSize:"13px",marginBottom:"16px"}}>
                    <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Posts</span><span style={{fontWeight:"600",color:"#fff"}}>{t.post_count?.toLocaleString()}</span></div>
                    <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Activity</span><span style={{fontWeight:"600",color:"#46d160"}}>{formatRate(t.rate_per_second)}</span></div>
                    <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>Media</span><span style={{fontWeight:"600",color:"#fff"}}>{t.downloaded_media}/{t.total_media}</span></div>
                    <div style={{background:"#141414",padding:"10px",borderRadius:"8px"}}><span style={{color:"#666",display:"block",fontSize:"11px",marginBottom:"2px"}}>ETA</span><span style={{fontWeight:"600",color:"#f9c300"}}>{formatEta(t.eta_seconds)}</span></div>
                  </div>
                  <div style={{background:"#141414",height:"8px",borderRadius:"4px",overflow:"hidden"}}>
                    <div style={{width:`${Math.min(100,t.progress_percent)}%`,background:"linear-gradient(90deg,#ff4500,#ff6a33)",height:"100%",borderRadius:"4px",transition:"width 0.5s ease"}}/>
                  </div>
                  <div style={{fontSize:"11px",color:"#555",marginTop:"8px",textAlign:"right"}}>{t.progress_percent}% of 1k posts</div>
                  {t.last_created && (
                    <div style={{fontSize:"11px",color:"#444",marginTop:"8px",display:"flex",alignItems:"center",gap:"4px"}}>
                      <span style={{width:"6px",height:"6px",background:"#46d160",borderRadius:"50%"}} />
                      Last scraped: {new Date(t.last_created).toLocaleString()}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Recent Activity */}
            <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px"}}>
              <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Recent Activity</h2>
              <span style={{fontSize:"12px",color:"#555",marginLeft:"4px"}}>live</span>
              <div style={{width:"6px",height:"6px",borderRadius:"50%",background:liveConnected?"#46d160":"#444",boxShadow:liveConnected?"0 0 6px #46d160":"none"}}/>
            </div>
            <div style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"16px",border:"1px solid #2a2a2a",overflow:"hidden",boxShadow:"0 4px 20px rgba(0,0,0,0.3)"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
                <thead>
                  <tr style={{background:"#141414",borderBottom:"1px solid #2a2a2a"}}>
                    {["Time","Subreddit","Author","Title"].map(h=>(
                      <th key={h} style={{padding:"14px 16px",textAlign:"left",color:"#666",fontWeight:"500",fontSize:"12px",textTransform:"uppercase",letterSpacing:"0.5px"}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <style>{`@keyframes rowFlash{0%{background:#1c2e00}60%{background:#111c00}100%{background:transparent}}.row-new{animation:rowFlash 4s ease-out forwards}`}</style>
                  {logs && logs.map(l=>(
                    <tr key={l.id} className={highlightedRows.has(l.id)?"row-new":""} style={{borderBottom:"1px solid #222",transition:"background 0.3s ease"}}>
                      <td style={{padding:"12px 16px",color:"#555"}}>{l.created_utc?new Date(l.created_utc).toLocaleTimeString():"-"}</td>
                      <td style={{padding:"12px 16px"}}><span style={{background:"#ff450022",color:"#ff4500",padding:"4px 8px",borderRadius:"4px",fontSize:"12px",fontWeight:"500"}}>{l.subreddit||"-"}</span></td>
                      <td style={{padding:"12px 16px",color:"#888"}}>{l.author||"-"}</td>
                      <td style={{padding:"12px 16px",maxWidth:"400px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#ccc"}}>{l.title||"-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>)}
        </div>
      )}

      {/* ── BROWSE TAB ── */}
      {activeTab === "browse" && (<>
        {newPostsAvailable > 0 && !searchResults && (
          <div onClick={refreshPosts} style={{position:"sticky",top:"73px",zIndex:90,margin:"0",padding:"12px 24px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",color:"#fff",textAlign:"center",cursor:"pointer",fontSize:"14px",fontWeight:"600",boxShadow:"0 4px 20px rgba(255,69,0,0.4)",transition:"all 0.2s ease",letterSpacing:"0.3px"}}>
            ↑ {newPostsAvailable} new post{newPostsAvailable>1?"s":""} — click to refresh
          </div>
        )}

        {/* ── FILTER / SORT BAR ── */}
        {!searchResults && (
          <div style={{borderBottom:"1px solid #1e1e1e",background:"#111",padding:"12px 24px"}}>
            <div style={{maxWidth:"1400px",margin:"0 auto",display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
              <span style={{fontSize:"12px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px",whiteSpace:"nowrap"}}>Filter</span>

              {/* Subreddit filter */}
              <input
                type="text"
                placeholder="r/ subreddit"
                value={filterSubreddit}
                onChange={e=>{
                  const v = e.target.value
                  setFilterSubreddit(v)
                  clearTimeout(searchTimeout._filterSubredddit)
                  searchTimeout._filterSubredddit = setTimeout(()=>{
                    const f = {...filtersRef.current, subreddit: v}
                    applyFilters(f)
                  }, 400)
                }}
                style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}
              />

              {/* Author filter */}
              <input
                type="text"
                placeholder="u/ author"
                value={filterAuthor}
                onChange={e=>{
                  const v = e.target.value
                  setFilterAuthor(v)
                  clearTimeout(searchTimeout._filterAuthor)
                  searchTimeout._filterAuthor = setTimeout(()=>{
                    const f = {...filtersRef.current, author: v}
                    applyFilters(f)
                  }, 400)
                }}
                style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}
              />

              {/* Media type filter */}
              <select
                value={filterMediaType}
                onChange={e=>{
                  const v = e.target.value
                  setFilterMediaType(v)
                  const f = {...filtersRef.current, mediaType: v}
                  applyFilters(f)
                }}
                style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:filterMediaType!=="all"?"#ff6a33":"#888",fontSize:"13px",cursor:"pointer",outline:"none"}}
              >
                <option value="all">All types</option>
                <option value="image">Images only</option>
                <option value="video">Videos only</option>
                <option value="text">Text only</option>
              </select>

              <span style={{fontSize:"12px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px",whiteSpace:"nowrap",marginLeft:"8px"}}>Sort</span>

              {/* Sort selector */}
              <select
                value={sortBy}
                onChange={e=>{
                  const v = e.target.value
                  setSortBy(v)
                  const f = {...filtersRef.current, sort: v}
                  applyFilters(f)
                }}
                style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:sortBy!=="newest"?"#ff6a33":"#888",fontSize:"13px",cursor:"pointer",outline:"none"}}
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="title_asc">Title A → Z</option>
                <option value="title_desc">Title Z → A</option>
              </select>

              {/* Clear filters */}
              {hasActiveFilters() && (
                <button
                  onClick={clearFilters}
                  style={{marginLeft:"auto",padding:"8px 14px",background:"#1e1e1e",border:"1px solid #ff450044",borderRadius:"8px",color:"#ff6a33",cursor:"pointer",fontSize:"12px",fontWeight:"500",whiteSpace:"nowrap"}}
                >
                  ✕ Clear filters
                </button>
              )}
            </div>
          </div>
        )}

        {searchResults && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"24px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Search Results <span style={{color:"#666",fontWeight:"400"}}>({searchResults.length})</span></h2>
              </div>
              <button onClick={()=>{setSearchResults(null);setSearch("")}} style={{padding:"10px 20px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"14px"}}>Clear Search</button>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"16px"}}>
              {searchResults.map(p=>(
                <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"14px",cursor:"pointer",border:"1px solid #2a2a2a",transition:"all 0.2s ease",boxShadow:"0 4px 12px rgba(0,0,0,0.2)"}}>
                  <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"6px"}}>{p.subreddit ? `r/${p.subreddit}` : ""}</div>
                  <div style={{fontWeight:"500",marginBottom:"8px",lineHeight:"1.4",color:"#e0e0e0"}}>{p.title}</div>
                  {p.author && <div style={{fontSize:"12px",color:"#555"}}>u/{p.author}</div>}
                </div>
              ))}
            </div>
          </div>
        )}

        {!searchResults && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"20px"}}>
              {posts.map(p=>(
                <div key={p.id} onClick={()=>setSelectedPost(p)}
                  onMouseEnter={()=>setHoveredCard(p.id)} onMouseLeave={()=>setHoveredCard(null)}
                  style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"16px",overflow:"hidden",cursor:"pointer",transition:"all 0.25s ease",transform:hoveredCard===p.id?"translateY(-4px)":"translateY(0)",boxShadow:hoveredCard===p.id?"0 12px 40px rgba(255,69,0,0.15)":"0 4px 12px rgba(0,0,0,0.3)",border:"1px solid #2a2a2a"}}>
                  {p.is_video ? (
                    <div style={{aspectRatio:"1",background:"#0a0a0a",position:"relative",overflow:"hidden"}}>
                      {/* Hover video preview */}
                      {hoveredCard===p.id && p.video_url && (p.video_url.includes("v.redd.it")||p.video_url.endsWith(".mp4")) ? (
                        <video
                          src={p.video_url}
                          autoPlay muted loop playsInline
                          style={{width:"100%",height:"100%",objectFit:"cover"}}
                        />
                      ) : (
                        <div style={{width:"100%",height:"100%",display:"flex",alignItems:"center",justifyContent:"center",background:"linear-gradient(135deg,#111 0%,#1a1a1a 100%)"}}>
                          <div style={{width:"64px",height:"64px",borderRadius:"50%",background:"rgba(255,69,0,0.15)",border:"2px solid rgba(255,69,0,0.4)",display:"flex",alignItems:"center",justifyContent:"center",transition:"all 0.2s ease",transform:hoveredCard===p.id?"scale(1.1)":"scale(1)"}}>
                            <div style={{width:0,height:0,borderTop:"12px solid transparent",borderBottom:"12px solid transparent",borderLeft:"20px solid #ff4500",marginLeft:"4px"}}/>
                          </div>
                        </div>
                      )}
                      {/* Video badge */}
                      <div style={{position:"absolute",top:"10px",left:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"3px 8px",display:"flex",alignItems:"center",gap:"5px",fontSize:"10px",fontWeight:"700",color:"#fff",letterSpacing:"0.5px",border:"1px solid rgba(255,255,255,0.1)"}}>
                        <div style={{width:0,height:0,borderTop:"5px solid transparent",borderBottom:"5px solid transparent",borderLeft:"8px solid #ff4500"}}/>
                        VIDEO
                      </div>
                      <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
                        <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
                      </div>
                    </div>
                  ) : p.url ? (
                    <div style={{aspectRatio:"1",background:"#141414",position:"relative",overflow:"hidden"}}>
                      <img src={p.url} style={{width:"100%",height:"100%",objectFit:"cover",transition:"transform 0.3s ease"}} onError={e=>e.target.style.display="none"}/>
                      <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
                        <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
                      </div>
                    </div>
                  ) : (
                    <div style={{padding:"24px",background:"linear-gradient(135deg,#1a1a1a 0%,#222 100%)",minHeight:"180px",display:"flex",flexDirection:"column"}}>
                      <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"12px"}}>{p.subreddit||"reddit"}</div>
                      <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4",color:"#fff"}}>{p.title}</div>
                      {p.selftext && <div style={{fontSize:"13px",color:"#777",lineHeight:"1.6",flex:1}}>{truncateText(p.selftext)}</div>}
                    </div>
                  )}
                  {(p.url || p.is_video) && (
                    <div style={{padding:"16px"}}>
                      <div style={{fontSize:"11px",color:"#666",textTransform:"uppercase",letterSpacing:"1px",marginBottom:"6px"}}>{p.subreddit||"reddit"}</div>
                      <div style={{fontSize:"14px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#ccc"}}>{p.title}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div ref={loader} style={{padding:"60px",textAlign:"center",color:"#444",fontSize:"14px"}}>
              <div style={{display:"inline-flex",alignItems:"center",gap:"8px"}}>
                <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#ff4500",borderRadius:"50%",animation:"spin 1s linear infinite"}}/>
                Loading more posts...
              </div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          </div>
        )}
      </>)}

      {/* ── RESET MODAL ── */}
      {resetModal && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:300,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>!resetLoading&&setResetModal(false)}>
          <div style={{background:"#0d0d0d",borderRadius:"20px",maxWidth:"480px",width:"100%",border:"1px solid #550000",boxShadow:"0 24px 80px rgba(200,0,0,0.3)"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"28px 28px 0"}}>
              <div style={{fontSize:"28px",marginBottom:"12px"}}>⚠️</div>
              <h2 style={{margin:"0 0 12px",fontSize:"22px",color:"#ff4444"}}>Reset All Data</h2>
              <p style={{margin:"0 0 8px",color:"#aaa",fontSize:"14px",lineHeight:"1.6"}}>This will permanently delete:</p>
              <ul style={{margin:"0 0 20px",color:"#888",fontSize:"13px",lineHeight:"2",paddingLeft:"20px"}}>
                <li>All posts, comments, media records and tags from the database</li>
                <li>All downloaded media files on disk</li>
                <li>The entire Redis download queue</li>
              </ul>
              {!resetResult ? (<>
                <p style={{margin:"0 0 12px",color:"#666",fontSize:"13px"}}>Type <strong style={{color:"#ff4444",fontFamily:"monospace"}}>RESET</strong> to confirm:</p>
                <input autoFocus type="text" value={resetInput} onChange={e=>setResetInput(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&resetInput==="RESET"&&!resetLoading&&doReset()}
                  placeholder="RESET"
                  style={{width:"100%",boxSizing:"border-box",padding:"12px 16px",borderRadius:"10px",border:`1px solid ${resetInput==="RESET"?"#ff4444":"#333"}`,background:"#141414",color:"#fff",fontSize:"16px",fontFamily:"monospace",outline:"none",marginBottom:"20px",transition:"border-color 0.2s"}}/>
              </>) : (
                <div style={{background:"#0a1a0a",border:"1px solid #1a4a1a",borderRadius:"10px",padding:"16px",marginBottom:"20px",fontSize:"13px",color:"#46d160"}}>
                  {resetResult.error
                    ? <span style={{color:"#ff4444"}}>Error: {resetResult.error}</span>
                    : <>✓ Reset complete — deleted {resetResult.deleted_files} files ({resetResult.deleted_mb} MB){resetResult.errors?.length>0&&<div style={{color:"#f9c300",marginTop:"4px"}}>{resetResult.errors.length} warnings</div>}</>
                  }
                </div>
              )}
            </div>
            <div style={{padding:"0 28px 28px",display:"flex",gap:"10px",justifyContent:"flex-end"}}>
              <button onClick={()=>setResetModal(false)} disabled={resetLoading}
                style={{padding:"12px 24px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"10px",color:"#888",cursor:"pointer",fontSize:"14px"}}>
                {resetResult?"Close":"Cancel"}
              </button>
              {!resetResult && (
                <button onClick={doReset} disabled={resetInput!=="RESET"||resetLoading}
                  style={{padding:"12px 24px",background:resetInput==="RESET"?"#cc0000":"#330000",border:"1px solid #550000",borderRadius:"10px",color:resetInput==="RESET"?"#fff":"#555",cursor:resetInput==="RESET"?"pointer":"not-allowed",fontSize:"14px",fontWeight:"600",transition:"all 0.2s"}}>
                  {resetLoading?"Resetting...":"Confirm Reset"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── POST DETAIL MODAL ── */}
      {selectedPost && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.9)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>setSelectedPost(null)}>
          <div style={{background:"#0d0d0d",borderRadius:"20px",maxWidth:"720px",width:"100%",maxHeight:"90vh",overflow:"auto",border:"1px solid #222",boxShadow:"0 24px 80px rgba(0,0,0,0.5)"}} onClick={e=>e.stopPropagation()}>
            {(selectedPost.is_video || selectedPost.video_url) ? (
              <div style={{background:"#000",position:"relative",borderRadius:"20px 20px 0 0",overflow:"hidden"}}>
                {selectedPost.video_url && (selectedPost.video_url.includes("v.redd.it")||selectedPost.video_url.endsWith(".mp4")) ? (
                  <video
                    src={selectedPost.video_url}
                    controls autoPlay muted loop playsInline
                    style={{width:"100%",maxHeight:"500px",display:"block",background:"#000"}}
                  />
                ) : selectedPost.video_url && (selectedPost.video_url.includes("youtube.com")||selectedPost.video_url.includes("youtu.be")) ? (
                  <div style={{position:"relative",paddingTop:"56.25%"}}>
                    <iframe
                      src={`https://www.youtube.com/embed/${selectedPost.video_url.match(/(?:v=|youtu\.be\/)([^&?/]+)/)?.[1]||""}`}
                      style={{position:"absolute",top:0,left:0,width:"100%",height:"100%",border:"none"}}
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                      allowFullScreen
                    />
                  </div>
                ) : (
                  <div style={{minHeight:"200px",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",gap:"16px",padding:"40px"}}>
                    <div style={{width:"80px",height:"80px",borderRadius:"50%",background:"rgba(255,69,0,0.15)",border:"2px solid rgba(255,69,0,0.4)",display:"flex",alignItems:"center",justifyContent:"center"}}>
                      <div style={{width:0,height:0,borderTop:"16px solid transparent",borderBottom:"16px solid transparent",borderLeft:"26px solid #ff4500",marginLeft:"6px"}}/>
                    </div>
                    {selectedPost.url && <a href={selectedPost.url} target="_blank" rel="noopener" style={{color:"#ff4500",fontSize:"13px",textDecoration:"none"}}>↗ Open video source</a>}
                  </div>
                )}
                <div style={{position:"absolute",top:"16px",left:"16px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"4px 10px",display:"flex",alignItems:"center",gap:"6px",fontSize:"11px",fontWeight:"700",color:"#fff",border:"1px solid rgba(255,255,255,0.1)"}}>
                  <div style={{width:0,height:0,borderTop:"5px solid transparent",borderBottom:"5px solid transparent",borderLeft:"8px solid #ff4500"}}/>
                  VIDEO
                </div>
                {selectedPost.url && (
                  <div style={{position:"absolute",top:"16px",right:"16px"}}>
                    <a href={selectedPost.url} target="_blank" rel="noopener" style={{background:"rgba(0,0,0,0.7)",color:"#fff",padding:"8px 14px",borderRadius:"8px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px"}}>↗ Open</a>
                  </div>
                )}
              </div>
            ) : selectedPost.url ? (
              <div style={{background:"#000",position:"relative"}}>
                <img src={selectedPost.url} style={{width:"100%",maxHeight:"450px",objectFit:"contain",borderRadius:"20px 20px 0 0"}} onError={e=>e.target.style.display="none"}/>
                <div style={{position:"absolute",top:"16px",right:"16px"}}>
                  <a href={selectedPost.url} target="_blank" rel="noopener" style={{background:"rgba(0,0,0,0.7)",color:"#fff",padding:"8px 14px",borderRadius:"8px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px"}}>↗ Open</a>
                </div>
              </div>
            ) : null}
            <div style={{padding:"28px"}}>
              <div style={{display:"flex",gap:"16px",fontSize:"13px",color:"#666",marginBottom:"20px",flexWrap:"wrap"}}>
                <span style={{color:"#ff4500",fontWeight:"600"}}>r/{selectedPost.subreddit||"reddit"}</span>
                <span>•</span>
                <span style={{color:"#888"}}>u/{selectedPost.author||"unknown"}</span>
                {selectedPost.created_utc && <><span>•</span><span style={{color:"#555"}}>{formatTime(selectedPost.created_utc)}</span></>}
                <span>•</span>
                <span style={{color:"#555"}}>ID: {selectedPost.id}</span>
              </div>
              <h2 style={{margin:"0 0 24px",fontSize:"24px",lineHeight:"1.4",fontWeight:"600",color:"#fff"}}>{selectedPost.title}</h2>

              {selectedPost.selftext && (
                <div style={{background:"linear-gradient(145deg,#141414,#1a1a1a)",padding:"24px",borderRadius:"14px",marginBottom:"24px",fontSize:"15px",lineHeight:"1.8",color:"#bbb",whiteSpace:"pre-wrap",border:"1px solid #222",maxHeight:"300px",overflow:"auto"}}>
                  {selectedPost.selftext}
                </div>
              )}

              {/* Tags */}
              <div style={{marginBottom:"20px"}}>
                <div style={{display:"flex",gap:"12px",alignItems:"center",marginBottom:"12px"}}>
                  <input type="text" placeholder="Add a tag..." value={tagInput}
                    onChange={e=>setTagInput(e.target.value)}
                    onKeyDown={e=>e.key==="Enter"&&addTag()}
                    style={{flex:1,padding:"12px 16px",borderRadius:"10px",border:"1px solid #333",background:"#141414",color:"#fff",fontSize:"14px",outline:"none"}}/>
                  <button onClick={addTag} style={{padding:"12px 24px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"10px",color:"#fff",cursor:"pointer",fontWeight:"600",fontSize:"14px",whiteSpace:"nowrap"}}>Add Tag</button>
                </div>
                {selectedPost.tags && selectedPost.tags.length > 0 && (
                  <div style={{display:"flex",gap:"8px",flexWrap:"wrap"}}>
                    {selectedPost.tags.map(t=>(
                      <span key={t} style={{background:"#ff450022",color:"#ff4500",padding:"5px 10px",borderRadius:"20px",fontSize:"12px",fontWeight:"500",display:"flex",alignItems:"center",gap:"6px"}}>
                        {t}
                        <button onClick={()=>removeTag(t)} style={{background:"none",border:"none",color:"#ff4500",cursor:"pointer",fontSize:"14px",lineHeight:1,padding:"0",opacity:0.7}}>×</button>
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Comments */}
              {selectedPost.comments === undefined && (
                <div style={{color:"#444",fontSize:"13px",padding:"8px 0"}}>Loading comments…</div>
              )}
              {selectedPost.comments && selectedPost.comments.length > 0 && (
                <div>
                  <div style={{fontSize:"13px",color:"#555",fontWeight:"600",textTransform:"uppercase",letterSpacing:"0.5px",marginBottom:"12px"}}>Comments ({selectedPost.comments.length})</div>
                  <div style={{display:"flex",flexDirection:"column",gap:"10px",maxHeight:"320px",overflow:"auto",paddingRight:"4px"}}>
                    {selectedPost.comments.map(c=>(
                      <div key={c.id} style={{background:"#141414",borderRadius:"10px",padding:"14px",border:"1px solid #1e1e1e"}}>
                        <div style={{display:"flex",gap:"10px",alignItems:"center",marginBottom:"8px"}}>
                          <span style={{color:"#ff4500",fontSize:"12px",fontWeight:"600"}}>u/{c.author||"[deleted]"}</span>
                          {c.created_utc && <span style={{color:"#444",fontSize:"11px"}}>{formatTime(c.created_utc)}</span>}
                        </div>
                        <div style={{color:"#bbb",fontSize:"14px",lineHeight:"1.6",whiteSpace:"pre-wrap"}}>{c.body}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {selectedPost.comments && selectedPost.comments.length === 0 && (
                <div style={{color:"#444",fontSize:"13px",padding:"8px 0"}}>No comments archived.</div>
              )}
            </div>
            <div style={{padding:"16px 28px",borderTop:"1px solid #1a1a1a",display:"flex",justifyContent:"flex-end"}}>
              <button onClick={()=>setSelectedPost(null)} style={{padding:"10px 20px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"13px"}}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

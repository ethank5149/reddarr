import {useEffect,useState,useRef,useCallback} from "react"
import {NavLink, useLocation} from "react-router-dom"
import axios from "axios"

// PWA Service Worker Registration
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}

// Touch device detection
const isTouchDevice = () => 'ontouchstart' in window || navigator.maxTouchPoints > 0

// Prevent default touch behaviors for gallery
const preventDefault = (e) => { if (e.touches.length > 1) e.preventDefault() }

export default function App(){
  const location = useLocation()
  const activeTab = location.pathname === "/" ? "browse" : location.pathname.slice(1)

  const [posts,setPosts]=useState([])
  const [search,setSearch]=useState("")
  const [searchResults,setSearchResults]=useState(null)
  const [selectedPost,setSelectedPost]=useState(null)
  const [galleryIdx,setGalleryIdx]=useState(0)
  const [archivePosts,setArchivePosts]=useState([])
  const [archiveOffset,setArchiveOffset]=useState(0)
  const archiveOffsetRef=useRef(0)
  const archiveFilteringRef=useRef(false)
  const [archiveFilterSubreddit,setArchiveFilterSubreddit]=useState("")
  const [archiveFilterAuthor,setArchiveFilterAuthor]=useState("")
  const [archiveFilterMediaTypes,setArchiveFilterMediaTypes]=useState([])
  const [archiveSortBy,setArchiveSortBy]=useState("last_added")
  const [archiveShowNsfw,setArchiveShowNsfw]=useState(true)
  const [archiveIsLoading,setArchiveIsLoading]=useState(false)
  const archiveLoader=useRef()
  const archiveFiltersRef=useRef({subreddit:"",author:"",mediaTypes:[],sort:"last_added",nsfw:true})
  const [archiveSearch,setArchiveSearch]=useState("")
  const [archiveSearchResults,setArchiveSearchResults]=useState(null)
  const archiveSearchTimeout=useRef()
  const [adminData,setAdminData]=useState(null)
  const [logs,setLogs]=useState([])
  const [hoveredCard,setHoveredCard]=useState(null)
  const [adminLoading, setAdminLoading] = useState(false)
  const [auditData, setAuditData] = useState(null)
  const [auditPosts, setAuditPosts] = useState([])
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditPostDetail, setAuditPostDetail] = useState(null)
  const [auditFilters, setAuditFilters] = useState({status: "", subreddit: ""})
  const [auditOffset, setAuditOffset] = useState(0)
  const auditOffsetRef = useRef(0)
  const [queueInfo, setQueueInfo] = useState(null)
  const [healthStatus, setHealthStatus] = useState(null)
  const [newPostsAvailable, setNewPostsAvailable] = useState(0)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [liveConnected, setLiveConnected] = useState(false)
  const [resetModal, setResetModal] = useState(false)
  const [resetInput, setResetInput] = useState("")
  const [resetLoading, setResetLoading] = useState(false)
  const [resetResult, setResetResult] = useState(null)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleteTargetId, setDeleteTargetId] = useState(null)
  const [highlightedRows, setHighlightedRows] = useState(new Set())
  const [addTargetType, setAddTargetType] = useState("subreddit")
  const [addTargetName, setAddTargetName] = useState("")
  const [isTouch, setIsTouch] = useState(false)
  const [swipeStart, setSwipeStart] = useState(null)
  const [headerHeight, setHeaderHeight] = useState(73)
  const [toasts, setToasts] = useState([])
  const [filterBarOpen, setFilterBarOpen] = useState(false)
  const [archiveFilterBarOpen, setArchiveFilterBarOpen] = useState(false)
  const [installPrompt, setInstallPrompt] = useState(null)
  const [showInstallBanner, setShowInstallBanner] = useState(false)

  // Detect touch device and handle safe areas
  useEffect(() => {
    const checkTouch = () => setIsTouch(isTouchDevice())
    checkTouch()
    
    // Handle safe area insets
    const updateHeaderHeight = () => {
      const viewportHeight = window.visualViewport?.height || window.innerHeight
      const diff = window.innerHeight - viewportHeight
      setHeaderHeight(73 + diff)
    }
    
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', updateHeaderHeight)
    }
    window.addEventListener('resize', updateHeaderHeight)
    
    return () => {
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', updateHeaderHeight)
      }
      window.removeEventListener('resize', updateHeaderHeight)
    }
  }, [])

  // PWA install prompt
  useEffect(() => {
    const handler = (e) => {
      e.preventDefault()
      setInstallPrompt(e)
      setShowInstallBanner(true)
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  // Escape key closes modals + keeps keyboard nav working
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') {
        if (selectedPost) { setSelectedPost(null); return }
        if (auditPostDetail) { setAuditPostDetail(null); return }
        if (deleteModal) { setDeleteModal(false); return }
        if (resetModal && !resetLoading) { setResetModal(false); return }
      }
      if (e.key === 'ArrowLeft' && selectedPost?.image_urls?.length > 1) {
        setGalleryIdx(i => Math.max(0, i - 1))
      }
      if (e.key === 'ArrowRight' && selectedPost?.image_urls?.length > 1) {
        setGalleryIdx(i => Math.min(selectedPost.image_urls.length - 1, i + 1))
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [selectedPost, auditPostDetail, deleteModal, resetModal, resetLoading])

  // Toast helper
  function showToast(message, type = 'info', duration = 3000) {
    const id = Date.now()
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration)
  }

  // Wrap native alert/confirm usages
  function toast(msg) { showToast(msg, 'info') }
  function toastSuccess(msg) { showToast(msg, 'success') }
  function toastError(msg) { showToast(msg, 'error') }

  // Thumbnail utility state
  const [thumbStats, setThumbStats] = useState(null)
  const [thumbJob, setThumbJob] = useState(null)       // current running job
  const [thumbJobResult, setThumbJobResult] = useState(null)  // last finished job
  const thumbPollRef = useRef(null)

  // Bulk archive state
  const [archiveStats, setArchiveStats] = useState(null)
  const [archiveJob, setArchiveJob] = useState(null)           // current running job
  const [archiveJobResult, setArchiveJobResult] = useState(null) // last finished job
  const archiveJobPollRef = useRef(null)
  const [archiveBulkFilter, setArchiveBulkFilter] = useState({
    target_type: "", target_name: "", before_days: "", media_status: ""
  })
  const [archivePanelOpen, setArchivePanelOpen] = useState(false)
  const [cardArchiving, setCardArchiving] = useState({})        // { "type:name": bool }

  // Backfill status
  const [backfillStatus, setBackfillStatus] = useState(null)
  const backfillPollRef = useRef(null)

  // Scrape trigger feedback
  const [scrapeTriggered, setScrapeTriggered] = useState(false)
  const [backfillTriggered, setBackfillTriggered] = useState(false)

  // Per-target card state: expanded panel, audit data, in-flight actions
  const [expandedCard, setExpandedCard] = useState(null)           // "type:name"
  const [cardAudit, setCardAudit] = useState({})                   // { "type:name": auditObj }
  const [cardAuditLoading, setCardAuditLoading] = useState({})     // { "type:name": bool }
  const [cardScraping, setCardScraping] = useState({})             // { "type:name": bool }
  const [cardBackfilling, setCardBackfilling] = useState({})       // { "type:name": bool }

  // Filter + sort state
  const [filterSubreddit, setFilterSubreddit] = useState("")
  const [filterAuthor, setFilterAuthor] = useState("")
  const [filterMediaTypes, setFilterMediaTypes] = useState([]) // array of: image | video | text
  const [showNsfw, setShowNsfw] = useState(() => {
    const saved = localStorage.getItem("showNsfw")
    return saved !== null ? saved === "true" : true
  })
  const [sortBy, setSortBy] = useState("last_added") // newest | oldest | title_asc | title_desc | last_added
  const [isLoading, setIsLoading] = useState(false)

  // Refs to avoid stale closures in async callbacks
  const offsetRef = useRef(0)
  const filtersRef = useRef({ 
    subreddit:"", author:"", mediaTypes:[], sort:"last_added", 
    nsfw: localStorage.getItem("showNsfw") !== null ? localStorage.getItem("showNsfw") === "true" : true 
  })
  const filteringRef = useRef(false)  // true while applyFilters fetch is in flight

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
            // Auto-refresh browse grid when sorted by last added
            if(filtersRef.current.sort === "last_added"){
              refreshPosts()
            } else {
              setNewPostsAvailable(n => n + data.new_posts.length)
            }
          }

          // Handle newly downloaded media - refresh to show new thumbnails
          if(data.new_media && data.new_media.length > 0){
            if(filtersRef.current.sort === "last_added"){
              refreshPosts()
            }
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
    if(activeTab === "admin"){
      if(!adminData) loadAdmin()
      loadThumbStats()
    }
    if(activeTab === "audit"){
      loadAuditSummary()
      loadAuditPosts()
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

  // Fetch full post detail (comments) when modal opens
  useEffect(()=>{
    if(!selectedPost?.id) return
    axios.get(`/api/post/${selectedPost.id}`)
      .then(r=>{
        if(!r.data) return
        setSelectedPost(prev => prev?.id === r.data.id ? {
          ...prev,
          comments: r.data.comments || [],
          created_utc: r.data.created_utc || prev?.created_utc,
          video_url: r.data.video_url ?? prev?.video_url,
          is_video: r.data.is_video ?? prev?.is_video,
          url: r.data.image_url ?? prev?.url,
          archived: r.data.archived ?? prev?.archived,
        } : prev)
      })
      .catch(()=>{})
  },[selectedPost?.id])

  function buildPostsQuery(offset, filtersOverride, archivedFlag=false){
    const f = filtersOverride || filtersRef.current
    const params = new URLSearchParams({ limit:"50", offset:String(offset), _t: Date.now().toString() })
    if(archivedFlag) params.set("archived","true")
    if(f.subreddit) params.set("subreddit", f.subreddit)
    if(f.author) params.set("author", f.author)
    if(f.mediaTypes && f.mediaTypes.length > 0){
      f.mediaTypes.forEach(mt => params.append("media_type", mt))
    }
    if(f.sort === "oldest"){ params.set("sort_by","created_utc"); params.set("sort_order","asc") }
    else if(f.sort === "title_asc"){ params.set("sort_by","title"); params.set("sort_order","asc") }
    else if(f.sort === "title_desc"){ params.set("sort_by","title"); params.set("sort_order","desc") }
    else if(f.sort === "last_added"){ params.set("sort_by","ingested_at"); params.set("sort_order","desc") }
    else { params.set("sort_by","created_utc"); params.set("sort_order","desc") }
    if(f.nsfw === false) params.set("nsfw", "exclude")
    return `/api/posts?${params.toString()}`
  }

  function mapPost(p){
    return { 
      id:p.id, title:p.title, 
      url:p.image_url, image_urls:p.image_urls, 
      video_url:p.video_url, video_urls:p.video_urls, 
      is_video:p.is_video, selftext:p.selftext, 
      subreddit:p.subreddit, author:p.author, 
      created_utc:p.created_utc, thumb_url:p.thumb_url, preview_url:p.preview_url,
      archived:p.archived
    }
  }

  function load(){
    if(filteringRef.current) return
    const currentOffset = offsetRef.current
    console.log("Loading posts from offset:", currentOffset)
    axios.get(buildPostsQuery(currentOffset))
    .then(r=>{
      console.log("API response:", r.status, typeof r.data, JSON.stringify(r.data).slice(0,200))
      if(filteringRef.current) return
      const newPosts = r.data.posts?.map(mapPost) || []
      console.log("Mapped posts:", newPosts.length)
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
      const newPosts = r.data.posts?.map(mapPost) || []
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
    filteringRef.current = true
    setIsLoading(true)
    setPosts([])
    axios.get(buildPostsQuery(0))
    .then(r=>{
      setPosts(r.data.posts?.map(mapPost) || [])
      offsetRef.current = 50
    }).catch(err=>{
      console.error("Failed to load posts:", err.response?.data || err.message || err)
    }).finally(()=>{
      filteringRef.current = false
      setIsLoading(false)
    })
  }

  function hasActiveFilters(){
    const f = filtersRef.current
    return f.subreddit || f.author || (f.mediaTypes && f.mediaTypes.length > 0) || f.sort !== "last_added"
  }

  function clearFilters(){
    const defaultFilters = { subreddit:"", author:"", mediaTypes:[], sort:"last_added", nsfw:true }
    setFilterSubreddit("")
    setFilterAuthor("")
    setFilterMediaTypes([])
    setSortBy("last_added")
    applyFilters(defaultFilters)
  }

  // ── Archive tab load helpers ──
  function loadArchive(){
    if(archiveFilteringRef.current) return
    const currentOffset = archiveOffsetRef.current
    axios.get(buildPostsQuery(currentOffset, archiveFiltersRef.current, true))
    .then(r=>{
      if(archiveFilteringRef.current) return
      const newPosts = r.data.map(mapPost)
      setArchivePosts(prev=>[...prev,...newPosts])
      archiveOffsetRef.current = currentOffset + 50
    }).catch(err=>console.error("Failed to load archive posts:", err))
  }

  function applyArchiveFilters(newFilters){
    archiveFiltersRef.current = newFilters
    archiveOffsetRef.current = 0
    archiveFilteringRef.current = true
    setArchiveIsLoading(true)
    setArchivePosts([])
    axios.get(buildPostsQuery(0, newFilters, true))
    .then(r=>{
      setArchivePosts(r.data.map(mapPost))
      archiveOffsetRef.current = 50
    }).catch(err=>console.error("Failed to load archive posts:", err))
    .finally(()=>{ archiveFilteringRef.current=false; setArchiveIsLoading(false) })
  }

  function clearArchiveFilters(){
    const d={subreddit:"",author:"",mediaTypes:[],sort:"last_added",nsfw:true}
    setArchiveFilterSubreddit(""); setArchiveFilterAuthor(""); setArchiveFilterMediaTypes([]); setArchiveSortBy("last_added")
    applyArchiveFilters(d)
  }

  function hasActiveArchiveFilters(){
    const f=archiveFiltersRef.current
    return f.subreddit||f.author||(f.mediaTypes&&f.mediaTypes.length>0)||f.sort!=="last_added"
  }

  function archivePost(postId){
    axios.post(`/api/post/${postId}/archive`)
      .then(()=>{
        setPosts(prev=>prev.filter(p=>p.id!==postId))
        setArchivePosts([])
        archiveOffsetRef.current=0
        if(selectedPost?.id===postId) setSelectedPost(prev=>({...prev,archived:true}))
        toastSuccess("Post hidden")
      })
      .catch(()=>toastError("Failed to hide post"))
  }

  function unarchivePost(postId){
    axios.post(`/api/post/${postId}/unarchive`)
      .then(()=>{
        setArchivePosts(prev=>prev.filter(p=>p.id!==postId))
        setPosts([])
        offsetRef.current=0
        if(selectedPost?.id===postId) setSelectedPost(prev=>({...prev,archived:false}))
        toastSuccess("Post unhidden")
      })
      .catch(()=>toastError("Failed to unhide post"))
  }

  function deletePost(postId){
    setDeleteTargetId(postId)
    setDeleteModal(true)
  }

  function confirmDeletePost(){
    if(!deleteTargetId) return
    axios.delete(`/api/post/${deleteTargetId}`)
      .then(()=>{
        setPosts(prev=>prev.filter(p=>p.id!==deleteTargetId))
        setArchivePosts(prev=>prev.filter(p=>p.id!==deleteTargetId))
        if(selectedPost?.id===deleteTargetId) setSelectedPost(null)
        toastSuccess("Post deleted")
      })
      .catch(()=>toastError("Failed to delete post"))
      .finally(()=>{
        setDeleteModal(false)
        setDeleteTargetId(null)
      })
  }

  function handleArchiveSearch(e){
    setArchiveSearch(e.target.value)
    clearTimeout(archiveSearchTimeout.current)
    if(!e.target.value.trim()){ setArchiveSearchResults(null); return }
    archiveSearchTimeout.current=setTimeout(()=>{
      axios.get(`/api/search?q=${encodeURIComponent(e.target.value)}&archived=true`)
        .then(r=>setArchiveSearchResults(r.data.map(p=>({id:p.id,title:p.title,subreddit:p.subreddit,author:p.author,created_utc:p.created_utc}))))
    },300)
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
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/toggle`).then(()=>loadAdmin()).catch(()=>toastError("Failed to toggle target"))
  }

  function setTargetStatus(ttype,name,status){
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/status?new_status=${status}`).then(()=>loadAdmin()).catch(()=>toastError("Failed to set status"))
  }

  function rescanTarget(ttype,name){
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/rescan`).then(()=>{ toastSuccess("Rescan queued"); loadAdmin() }).catch(()=>toastError("Failed to rescan target"))
  }

  function scrapeNow(){
    axios.post("/api/admin/scrape")
      .then(()=>{ setScrapeTriggered(true); setTimeout(()=>setScrapeTriggered(false), 3000) })
      .catch(()=>toastError("Failed to trigger scrape"))
  }

  function triggerBackfill(){
    axios.post(`/api/admin/backfill?passes=2&workers=3`)
      .then(()=>{ 
        setBackfillTriggered(true); 
        startBackfillPoll();
        setTimeout(()=>setBackfillTriggered(false), 3000) 
      })
      .catch(()=>toastError("Failed to trigger backfill"))
  }

  function startBackfillPoll(){
    if(backfillPollRef.current) clearInterval(backfillPollRef.current)
    backfillPollRef.current = setInterval(()=>{
      axios.get("/api/admin/backfill/status")
        .then(r=>{
          setBackfillStatus(r.data)
          if(r.data.status === "done" || r.data.status === "partial" || r.data.status === "none"){
            clearInterval(backfillPollRef.current)
            backfillPollRef.current = null
          }
        })
        .catch(()=>setBackfillStatus(null))
    }, 2000)
  }

  function stopBackfillPoll(){
    if(backfillPollRef.current){ clearInterval(backfillPollRef.current); backfillPollRef.current = null }
  }

  useEffect(()=>{
    return () => { if(backfillPollRef.current) clearInterval(backfillPollRef.current) }
  }, [])

  function deleteTarget(ttype,name){
    const removeOnly = window.confirm(`Delete target ${ttype}:${name}?\n\nClick OK to remove from scrape list only (keeps posts and media).\nClick Cancel to abort.`)
    if (!removeOnly) return

    const shouldPrune = window.confirm(`Also delete all posts and media associated with ${name}?\n\nClick OK to delete posts and media.\nClick Cancel to keep them.`)
    if (!shouldPrune) {
      axios.delete(`/api/admin/target/${ttype}/${encodeURIComponent(name)}`).then(()=>{ toastSuccess("Target removed"); loadAdmin() }).catch(()=>toastError("Failed to delete target"))
    } else {
      const alsoDeleteFiles = window.confirm("Also delete downloaded media files from disk? (This cannot be undone)")
      axios.delete(`/api/admin/target/${ttype}/${encodeURIComponent(name)}?prune=true&delete_files=${alsoDeleteFiles}`)
        .then(r=>{ toastSuccess(`Deleted: ${r.data.deleted_posts} posts, ${r.data.deleted_media} media, ${r.data.deleted_files} files`); loadAdmin() })
        .catch(()=>toastError("Failed to delete target"))
    }
  }

  function addTarget(){
    const name = addTargetName.trim()
    if(!name) return
    axios.post(`/api/admin/target/${addTargetType}?name=${encodeURIComponent(name)}`)
      .then(()=>{ setAddTargetName(""); toastSuccess(`Added ${addTargetType}: ${name}`); loadAdmin() })
      .catch(()=>toastError("Failed to add target"))
  }

  function toggleCardExpand(ttype, name){
    const key = `${ttype}:${name}`
    if(expandedCard === key){
      setExpandedCard(null)
    } else {
      setExpandedCard(key)
      // Auto-load audit if not already loaded
      if(!cardAudit[key] && !cardAuditLoading[key]){
        fetchCardAudit(ttype, name)
      }
    }
  }

  function fetchCardAudit(ttype, name){
    const key = `${ttype}:${name}`
    setCardAuditLoading(prev => ({...prev, [key]: true}))
    axios.get(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/audit`)
      .then(r => {
        setCardAudit(prev => ({...prev, [key]: r.data}))
        setCardAuditLoading(prev => ({...prev, [key]: false}))
      })
      .catch(() => {
        setCardAuditLoading(prev => ({...prev, [key]: false}))
        toastError(`Audit failed for ${ttype}:${name}`)
      })
  }

  function scrapeTargetNow(ttype, name){
    const key = `${ttype}:${name}`
    setCardScraping(prev => ({...prev, [key]: true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/scrape`)
      .then(() => {
        toastSuccess(`Scrape triggered for ${ttype === "subreddit" ? "r/" : "u/"}${name}`)
        setTimeout(() => setCardScraping(prev => ({...prev, [key]: false})), 3000)
      })
      .catch(() => {
        toastError(`Failed to trigger scrape for ${name}`)
        setCardScraping(prev => ({...prev, [key]: false}))
      })
  }

  function backfillTargetNow(ttype, name){
    const key = `${ttype}:${name}`
    setCardBackfilling(prev => ({...prev, [key]: true}))
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/backfill?passes=2&workers=3`)
      .then(() => {
        toastSuccess(`Backfill triggered for ${ttype === "subreddit" ? "r/" : "u/"}${name}`)
        startBackfillPoll()
        setTimeout(() => setCardBackfilling(prev => ({...prev, [key]: false})), 3000)
      })
      .catch(() => {
        toastError(`Failed to trigger backfill for ${name}`)
        setCardBackfilling(prev => ({...prev, [key]: false}))
      })
  }

  function clearQueue(){
    if(!window.confirm("Clear the entire download queue?")) return
    axios.delete("/api/admin/queue").then(()=>{ toastSuccess("Queue cleared"); loadAdmin() }).catch(()=>toastError("Failed to clear queue"))
  }

  function doReset(){
    if(resetInput !== "RESET") return
    setResetLoading(true)
    axios.delete("/api/admin/reset?confirm=RESET")
      .then(r=>{
        setResetResult(r.data)
        setResetLoading(false)
        setPosts([]); offsetRef.current=0; setNewPostsAvailable(0); setLogs([])
        setArchivePosts([]); archiveOffsetRef.current=0
        loadAdmin()
      })
      .catch(err=>{
        setResetResult({error: err.response?.data?.detail || err.message})
        setResetLoading(false)
      })
  }

  function loadThumbStats(){
    axios.get("/api/admin/thumbnails/stats")
      .then(r=>setThumbStats(r.data))
      .catch(()=>setThumbStats(null))
  }

  function startThumbPoll(jobId){
    if(thumbPollRef.current) clearInterval(thumbPollRef.current)
    thumbPollRef.current = setInterval(()=>{
      axios.get(`/api/admin/thumbnails/job/${jobId}`)
        .then(r=>{
          setThumbJob(r.data)
          if(r.data.status==="done"){
            clearInterval(thumbPollRef.current)
            thumbPollRef.current = null
            setThumbJobResult(r.data)
            setThumbJob(null)
            loadThumbStats()
          }
        })
        .catch(()=>{
          clearInterval(thumbPollRef.current)
          thumbPollRef.current = null
        })
    }, 1000)
  }

  function runThumbBackfill(){
    setThumbJobResult(null)
    axios.post("/api/admin/thumbnails/backfill")
      .then(r=>{ setThumbJob({status:"pending", total:r.data.total, done:0, skipped:0, errors:[]}); startThumbPoll(r.data.job_id) })
      .catch(err=>toastError("Backfill failed: " + (err.response?.data?.detail||err.message)))
  }

  function runThumbRebuildAll(){
    if(!window.confirm("Regenerate ALL thumbnails? This will overwrite every existing thumbnail and may take a while.")) return
    setThumbJobResult(null)
    axios.post("/api/admin/thumbnails/rebuild-all")
      .then(r=>{ setThumbJob({status:"pending", total:r.data.total, done:0, skipped:0, errors:[]}); startThumbPoll(r.data.job_id) })
      .catch(err=>toastError("Rebuild failed: " + (err.response?.data?.detail||err.message)))
  }

  function runThumbPurgeOrphans(){
    if(!window.confirm("Delete all orphan thumbnail files (on disk but not in DB)?")) return
    axios.post("/api/admin/thumbnails/purge-orphans")
      .then(r=>{ toastSuccess(`Deleted ${r.data.deleted} orphan file(s), freed ${r.data.freed_mb} MB`); loadThumbStats() })
      .catch(err=>toastError("Purge failed: " + (err.response?.data?.detail||err.message)))
  }

  // ── Bulk Archive functions ──
  function loadArchiveStats(){
    axios.get("/api/admin/archive/stats")
      .then(r=>setArchiveStats(r.data))
      .catch(()=>setArchiveStats(null))
  }

  function startArchiveJobPoll(jobId){
    if(archiveJobPollRef.current) clearInterval(archiveJobPollRef.current)
    archiveJobPollRef.current = setInterval(()=>{
      axios.get(`/api/admin/archive/job/${jobId}`)
        .then(r=>{
          setArchiveJob(r.data)
          if(r.data.status === "done"){
            clearInterval(archiveJobPollRef.current)
            archiveJobPollRef.current = null
            setArchiveJobResult(r.data)
            setArchiveJob(null)
            loadArchiveStats()
            loadAdmin()
          }
        })
        .catch(()=>{
          clearInterval(archiveJobPollRef.current)
          archiveJobPollRef.current = null
        })
    }, 1500)
  }

  function runArchiveAll(){
    if(!window.confirm(`Archive ALL unarchived posts?\n\nThis will mark every active post as hidden and move their media files. This can take a while for large collections.`)) return
    setArchiveJobResult(null)
    axios.post("/api/admin/archive/all")
      .then(r=>{
        if(!r.data.job_id){ toastSuccess(r.data.message || "Nothing to archive"); return }
        setArchiveJob({status:"pending", total:r.data.total, done:0, skipped:0, files_moved:0, errors:[]})
        startArchiveJobPoll(r.data.job_id)
        toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts…`)
      })
      .catch(err=>toastError("Archive all failed: " + (err.response?.data?.detail||err.message)))
  }

  function runBulkArchiveFiltered(){
    const f = archiveBulkFilter
    const params = new URLSearchParams()
    if(f.target_type && f.target_name){ params.set("target_type", f.target_type); params.set("target_name", f.target_name) }
    if(f.before_days) params.set("before_days", f.before_days)
    if(f.media_status) params.set("media_status", f.media_status)
    // First dry run to show count
    axios.post(`/api/admin/archive/bulk?${params.toString()}&dry_run=true`)
      .then(r=>{
        if(r.data.post_count === 0){ toastSuccess("No posts match these filters"); return }
        if(!window.confirm(`Archive ${r.data.post_count.toLocaleString()} post(s) matching the current filters?`)) return
        setArchiveJobResult(null)
        return axios.post(`/api/admin/archive/bulk?${params.toString()}`)
          .then(res=>{
            if(!res.data.job_id){ toastSuccess(res.data.message || "Nothing to archive"); return }
            setArchiveJob({status:"pending", total:res.data.total, done:0, skipped:0, files_moved:0, errors:[]})
            startArchiveJobPoll(res.data.job_id)
            toastSuccess(`Archiving ${res.data.total.toLocaleString()} posts…`)
          })
      })
      .catch(err=>toastError("Bulk archive failed: " + (err.response?.data?.detail||err.message)))
  }

  function runArchiveTarget(ttype, name){
    const key = `${ttype}:${name}`
    setCardArchiving(prev=>({...prev, [key]:true}))
    // Dry run first
    axios.post(`/api/admin/target/${ttype}/${encodeURIComponent(name)}/archive-all`)
      .then(r=>{
        if(!r.data.job_id){
          toastSuccess(r.data.message || "Nothing to archive")
          setCardArchiving(prev=>({...prev, [key]:false}))
          return
        }
        setArchiveJob({status:"pending", total:r.data.total, done:0, skipped:0, files_moved:0, errors:[]})
        startArchiveJobPoll(r.data.job_id)
        toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts from ${ttype==="subreddit"?"r/":"u/"}${name}`)
        setCardArchiving(prev=>({...prev, [key]:false}))
      })
      .catch(err=>{
        toastError(`Archive failed: ` + (err.response?.data?.detail||err.message))
        setCardArchiving(prev=>({...prev, [key]:false}))
      })
  }

  useEffect(()=>{
    return ()=>{ if(archiveJobPollRef.current) clearInterval(archiveJobPollRef.current) }
  }, [])

  // Load archive stats when admin tab opens
  useEffect(()=>{
    if(activeTab === "admin" && !archiveStats) loadArchiveStats()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab])

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
    return text.length>len ? text.substring(0,len)+"…" : text
  }

  function formatTime(iso){
    if(!iso) return ""
    try{ return new Date(iso).toLocaleString() }catch{ return iso }
  }

  function loadAuditSummary(){
    axios.get("/api/admin/audit/summary")
      .then(r=>setAuditData(r.data))
      .catch(()=>setAuditData(null))
  }

  function loadAuditPosts(offset=0, status="", subreddit=""){
    setAuditLoading(true)
    const params = new URLSearchParams({limit:"50", offset:String(offset)})
    if(status) params.set("status_filter", status)
    if(subreddit) params.set("subreddit", subreddit)
    axios.get(`/api/admin/audit/posts?${params.toString()}`)
      .then(r=>{ setAuditPosts(r.data.posts); setAuditLoading(false) })
      .catch(()=>{ setAuditPosts([]); setAuditLoading(false) })
  }

  function loadAuditPostDetail(postId){
    axios.get(`/api/admin/audit/post/${postId}`)
      .then(r=>setAuditPostDetail(r.data))
      .catch(()=>setAuditPostDetail(null))
  }

  // Infinite scroll with cleanup
  useEffect(()=>{
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !searchResults) load()
    })
    if(loader.current) obs.observe(loader.current)
    return ()=> obs.disconnect()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[loader.current, searchResults])

  // Archive infinite scroll
  useEffect(()=>{
    const obs = new IntersectionObserver(entries=>{
      if(entries[0].isIntersecting && !archiveSearchResults) loadArchive()
    })
    if(archiveLoader.current) obs.observe(archiveLoader.current)
    return ()=> obs.disconnect()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[archiveLoader.current, archiveSearchResults])

  // Load archive posts when archive tab is first opened
  useEffect(()=>{
    if(activeTab==="archive" && archivePosts.length===0 && archiveOffsetRef.current===0){
      loadArchive()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[activeTab])

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
            image_url: p.image_url,
            video_url: p.video_url,
            thumb_url: p.thumb_url,
            is_video: p.is_video,
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
      {connected?"LIVE":"connecting…"}
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>
    </div>
  )

  // Mobile touch handlers for gallery swipe
  const handleTouchStart = (e) => {
    if (e.touches.length === 1) {
      setSwipeStart(e.touches[0].clientX)
    }
  }
  
  const handleTouchMove = (e) => {
    if (!swipeStart || !selectedPost?.image_urls?.length) return
    const delta = e.touches[0].clientX - swipeStart
    if (Math.abs(delta) > 50) {
      if (delta > 0 && galleryIdx > 0) {
        setGalleryIdx(i => i - 1)
      } else if (delta < 0 && galleryIdx < selectedPost.image_urls.length - 1) {
        setGalleryIdx(i => i + 1)
      }
      setSwipeStart(null)
    }
  }
  
  const handleTouchEnd = () => setSwipeStart(null)

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
    <div style={{minHeight:"100vh",background:"#0d0d0d",color:"#fff",fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif",paddingBottom:"env(safe-area-inset-bottom, 0)"}}>
      <header style={{
        padding:"16px 24px",
        paddingTop:"max(16px, env(safe-area-inset-top, 16px))",
        background:"linear-gradient(180deg,#1a1a1a 0%,#141414 100%)",
        borderBottom:"1px solid #222",
        position:"sticky",
        top:0,
        zIndex:100,
        backdropFilter:"blur(20px)",
        WebkitBackdropFilter:"blur(20px)",
        backgroundColor:"rgba(20,20,20,0.95)"
      }}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto",gap:"12px"}}>
          <div style={{display:"flex",alignItems:"center",gap:"16px",flexShrink:0}}>
            <div style={{position:"relative"}}>
              <img src="/icon.png" alt="Logo" style={{width:"40px",height:"40px",borderRadius:"12px",boxShadow:"0 4px 12px rgba(255,69,0,0.3)"}} />
              <div style={{position:"absolute",bottom:"-2px",right:"-2px",width:"10px",height:"10px",background:"#46d160",borderRadius:"50%",border:"2px solid #141414"}} />
            </div>
            <h1 style={{margin:0,fontSize:"20px",fontWeight:"700",background:"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",backgroundClip:"text",whiteSpace:"nowrap"}}>Reddit Archive</h1>
            <div style={{display:"flex",gap:"4px",background:"#1a1a1a",padding:"4px",borderRadius:"10px"}}>
              {[
                {to:"/",label:"Browse",icon:"⊞"},
                {to:"/archive",label:"Hidden",icon:"👁"},
                {to:"/audit",label:"Audit",icon:"✓"},
                {to:"/admin",label:"Admin",icon:"⚙"}
              ].map(tab=>(
                <NavLink key={tab.to} to={tab.to} end={tab.to==="/"} style={({isActive})=>({
                  padding:"8px 14px",
                  background:isActive?"linear-gradient(135deg,#ff4500 0%,#ff6a33 100%)":"transparent",
                  border:"none",
                  borderRadius:"8px",
                  color:"#fff",
                  cursor:"pointer",
                  fontWeight:isActive?"600":"400",
                  fontSize:"13px",
                  display:"flex",
                  alignItems:"center",
                  gap:"6px",
                  transition:"background 0.2s ease, color 0.2s ease",
                  textDecoration:"none",
                  whiteSpace:"nowrap"
                })}>
                  <span style={{fontSize:"14px"}}>{tab.icon}</span><span className="hide-on-mobile">{tab.label}</span>
                </NavLink>
              ))}
            </div>
            <LiveDot connected={liveConnected}/>
          </div>
          <div style={{display:"flex",alignItems:"center",gap:"12px",flexWrap:"wrap",justifyContent:"flex-end"}}>
            {queueInfo && (
              <div style={{fontSize:"12px",color:"#555",display:"flex",alignItems:"center",gap:"6px"}}>
                <span style={{color:"#333"}}>queue:</span>
                <span style={{color:queueInfo.queue_length>0?"#f9c300":"#46d160",fontWeight:"600",fontVariantNumeric:"tabular-nums"}}>{(queueInfo.queue_length||0).toLocaleString()}</span>
              </div>
            )}
            <div style={{position:"relative"}}>
              <span style={{position:"absolute",left:"14px",top:"50%",transform:"translateY(-50%)",color:"#666",fontSize:"16px"}}>⌕</span>
              <input type="search" inputMode="search" enterKeyHint="search" placeholder="Search…" aria-label="Search posts" autoComplete="off" spellCheck={false} value={search} onChange={handleSearch}
                style={{padding:"12px 16px 12px 42px",borderRadius:"24px",border:"1px solid #333",width:"200px",minWidth:"140px",background:"#1a1a1a",color:"#fff",fontSize:"14px",outline:"none",transition:"border-color 0.2s ease, box-shadow 0.2s ease",boxShadow:"0 2px 8px rgba(0,0,0,0.2)"}}/>
            </div>
          </div>
        </div>
      </header>

      {/* ── AUDIT TAB ── */}
      {activeTab === "audit" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"24px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
              <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#46d160,#2da64d)",borderRadius:"2px"}} />
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Audit Dashboard</h2>
              <span style={{fontSize:"12px",color:"#555",marginLeft:"4px"}}>Hidden Assets</span>
            </div>
            <button onClick={()=>{loadAuditSummary();loadAuditPosts()}} style={{padding:"8px 16px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"13px"}}>↻ Refresh</button>
          </div>

          {auditData && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:"16px",marginBottom:"32px"}}>
              {[
                {label:"Total Posts",value:auditData.total_archived_posts,color:"#fff",icon:"📦"},
                {label:"Posts All OK",value:auditData.posts_all_ok,color:"#46d160",icon:"✓"},
                {label:"Posts w/Issues",value:auditData.posts_with_issues,color:auditData.posts_with_issues>0?"#ff4500":"#46d160",icon:"⚠"},
                {label:"Media OK",value:auditData.media_ok,color:"#46d160",icon:"✓"},
                {label:"Media Missing",value:auditData.media_missing,color:auditData.media_missing>0?"#ff4500":"#46d160",icon:"✗"},
              ].map(s=>(
                <div key={s.label} style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"16px",borderRadius:"12px",border:"1px solid #2a2a2a"}}>
                  <div style={{fontSize:"11px",color:"#666",marginBottom:"6px",textTransform:"uppercase"}}>{s.label}</div>
                  <div style={{fontSize:"24px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{s.value?.toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}

          {auditData && auditData.posts_with_issues===0 && (
            <div style={{background:"#0d2818",border:"1px solid #1a4a1a",borderRadius:"12px",padding:"20px",marginBottom:"24px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{fontSize:"24px"}}>✓</div>
                <div><div style={{fontSize:"16px",fontWeight:"600",color:"#46d160"}}>All Assets Verified</div>
                <div style={{fontSize:"13px",color:"#888"}}>Every archived media file is present and accessible.</div></div>
              </div>
            </div>
          )}

          {auditData && auditData.posts_with_issues>0 && (
            <div style={{background:"#2d1a00",border:"1px solid #4a3a00",borderRadius:"12px",padding:"20px",marginBottom:"24px"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{fontSize:"24px"}}>⚠</div>
                <div><div style={{fontSize:"16px",fontWeight:"600",color:"#ff4500"}}>{auditData.posts_with_issues} Posts Need Attention</div>
                <div style={{fontSize:"13px",color:"#888"}}>Some media files missing - review details below.</div></div>
              </div>
            </div>
          )}

          <div style={{display:"flex",gap:"12px",marginBottom:"20px"}}>
            <select aria-label="Filter by status" value={auditFilters.status} onChange={e=>{setAuditFilters(f=>({...f,status:e.target.value}));auditOffsetRef.current=0;setAuditOffset(0);loadAuditPosts(0,e.target.value,auditFilters.subreddit)}}
              style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#ccc",fontSize:"13px"}}>
              <option value="">All statuses</option>
              <option value="ok">All OK</option>
              <option value="missing">Has Missing</option>
            </select>
            <input type="text" placeholder="r/ subreddit…" aria-label="Filter by subreddit" autoComplete="off" spellCheck={false} value={auditFilters.subreddit}
              onChange={e=>{setAuditFilters(f=>({...f,subreddit:e.target.value}));auditOffsetRef.current=0;setAuditOffset(0);loadAuditPosts(0,auditFilters.status,e.target.value)}}
              style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"13px",width:"140px"}}/>
          </div>

          <div style={{background:"#1e1e1e",borderRadius:"12px",border:"1px solid #2a2a2a",overflow:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:"13px"}}>
              <thead><tr style={{background:"#141414"}}>
                {["Status","Subreddit","Title","Media","Date"].map(h=>(
                  <th key={h} style={{padding:"12px 16px",textAlign:"left",color:"#666",fontSize:"11px",textTransform:"uppercase"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {auditPosts.map(p=>(
                  <tr key={p.id} onClick={()=>loadAuditPostDetail(p.id)} style={{cursor:"pointer",borderBottom:"1px solid #222"}} onMouseEnter={e=>e.currentTarget.style.background="#1a1a1a"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                    <td style={{padding:"12px 16px"}}>
                      {p.status==="ok" && <span style={{background:"#0d2818",color:"#46d160",padding:"3px 8px",borderRadius:"4px",fontSize:"11px"}}>✓ OK</span>}
                      {p.status==="partial" && <span style={{background:"#2d2000",color:"#f9c300",padding:"3px 8px",borderRadius:"4px",fontSize:"11px"}}>⚠ Partial</span>}
                      {p.status==="all_missing" && <span style={{background:"#2d0000",color:"#ff4500",padding:"3px 8px",borderRadius:"4px",fontSize:"11px"}}>✗ Missing</span>}
                      {p.status==="no_media" && <span style={{background:"#1a1a1a",color:"#666",padding:"3px 8px",borderRadius:"4px",fontSize:"11px"}}>— None</span>}
                    </td>
                    <td style={{padding:"12px 16px",color:"#ff4500"}}>{p.subreddit}</td>
                    <td style={{padding:"12px 16px",maxWidth:"300px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.title}</td>
                    <td style={{padding:"12px 16px",fontVariantNumeric:"tabular-nums"}}><span style={{color:p.media_missing>0?"#ff4500":"#46d160"}}>{p.media_ok}</span>/{p.media_count}</td>
                    <td style={{padding:"12px 16px",color:"#555"}}>{p.created_utc?new Date(p.created_utc).toLocaleDateString():"-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {auditPosts.length===0 && !auditLoading && <div style={{padding:"30px",textAlign:"center",color:"#555"}}>No archived posts.</div>}
            {auditLoading && <div style={{padding:"30px",textAlign:"center",color:"#555"}}>Loading…</div>}
          </div>

          {auditPosts.length > 0 && (
            <div style={{display:"flex",justifyContent:"center",gap:"8px",marginTop:"16px"}}>
              <button onClick={()=>{const o=Math.max(0,auditOffsetRef.current-50);auditOffsetRef.current=o;setAuditOffset(o);loadAuditPosts(o,auditFilters.status,auditFilters.subreddit)}} disabled={auditOffset===0}
                style={{padding:"8px 16px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:auditOffset===0?"#444":"#888",cursor:auditOffset===0?"not-allowed":"pointer"}}>← Prev</button>
              <button onClick={()=>{const o=auditOffsetRef.current+50;auditOffsetRef.current=o;setAuditOffset(o);loadAuditPosts(o,auditFilters.status,auditFilters.subreddit)}} disabled={auditPosts.length<50}
                style={{padding:"8px 16px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:auditPosts.length<50?"#444":"#888",cursor:auditPosts.length<50?"not-allowed":"pointer"}}>Next →</button>
            </div>
          )}
        </div>
      )}

      {auditPostDetail && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.9)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px"}} onClick={()=>setAuditPostDetail(null)}>
          <div style={{background:"#0d0d0d",borderRadius:"16px",maxWidth:"600px",width:"100%",maxHeight:"80vh",overflow:"auto",border:"1px solid #222"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"20px",borderBottom:"1px solid #1a1a1a"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start"}}>
                <div>
                  <div style={{fontSize:"11px",color:"#ff4500",marginBottom:"4px"}}>r/{auditPostDetail.subreddit}</div>
                  <div style={{fontSize:"16px",fontWeight:"600"}}>{auditPostDetail.title}</div>
                </div>
                {auditPostDetail.overall_status==="ok" && <span style={{background:"#0d2818",color:"#46d160",padding:"4px 10px",borderRadius:"6px",fontSize:"11px"}}>✓ OK</span>}
                {auditPostDetail.overall_status==="partial" && <span style={{background:"#2d2000",color:"#f9c300",padding:"4px 10px",borderRadius:"6px",fontSize:"11px"}}>⚠ Partial</span>}
                {auditPostDetail.overall_status==="all_missing" && <span style={{background:"#2d0000",color:"#ff4500",padding:"4px 10px",borderRadius:"6px",fontSize:"11px"}}>✗ Missing</span>}
              </div>
            </div>
            <div style={{padding:"20px"}}>
              {auditPostDetail.media.length===0 && <div style={{color:"#555"}}>No media items.</div>}
              {auditPostDetail.media.map(m=>(
                <div key={m.id} style={{background:"#141414",borderRadius:"8px",padding:"12px",marginBottom:"8px",border:m.resolved_status==="ok"?"1px solid #1a3a1a":"1px solid #3a1a1a"}}>
                  <div style={{marginBottom:"4px"}}>
                    {m.resolved_status==="ok" && <span style={{color:"#46d160",fontSize:"11px"}}>✓ Available</span>}
                    {m.resolved_status==="missing_file" && <span style={{color:"#ff4500",fontSize:"11px"}}>✗ File Missing</span>}
                    {m.resolved_status==="pending" && <span style={{color:"#7193ff",fontSize:"11px"}}>⏳ Pending</span>}
                    {m.resolved_status==="failed" && <span style={{color:"#ff4500",fontSize:"11px"}}>✗ Failed</span>}
                  </div>
                  <div style={{fontSize:"12px",color:"#888",wordBreak:"break-all"}}>{m.url}</div>
                  {m.file_path && <div style={{fontSize:"11px",color:"#555",marginTop:"4px"}}>File: {m.file_exists?"✓":"✗"} | {m.file_path}</div>}
                </div>
              ))}
            </div>
            <div style={{padding:"16px 20px",borderTop:"1px solid #1a1a1a",display:"flex",justifyContent:"flex-end"}}>
              <button onClick={()=>setAuditPostDetail(null)} style={{padding:"8px 16px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"6px",color:"#888"}}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ── ADMIN TAB ── */}
      {activeTab === "admin" && (
        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          {adminLoading && <div style={{textAlign:"center",padding:"40px",color:"#666"}}>Loading admin data…</div>}
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
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(160px,1fr))",gap:"16px",marginBottom:"40px"}}>
              {[
                {label:"Active Posts",value:adminData.total_posts,color:"#ff4500",icon:"📄"},
                {label:"Hidden Posts",value:adminData.archived_posts,color:"#888",icon:"👁"},
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
            {/* ── Archive Manager ── */}
            <div style={{marginBottom:"40px"}}>
              {/* Section header */}
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:"12px",marginBottom:"16px",flexWrap:"wrap"}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#46d160,#2ea84e)",borderRadius:"2px"}} />
                  <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Archive Manager</h2>
                  {archiveStats && (
                    <span style={{fontSize:"12px",color:"#555",background:"#1a1a1a",padding:"3px 8px",borderRadius:"12px",border:"1px solid #2a2a2a"}}>
                      {archiveStats.archive_pct}% archived
                    </span>
                  )}
                </div>
                <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                  <button onClick={loadArchiveStats} style={{padding:"6px 12px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"12px"}}>↻ Refresh</button>
                  <button
                    onClick={()=>setArchivePanelOpen(o=>!o)}
                    style={{padding:"6px 14px",background:archivePanelOpen?"#1a2a1a":"#1a2a1a",border:`1px solid ${archivePanelOpen?"#46d160":"#2a4a2a"}`,borderRadius:"8px",color:archivePanelOpen?"#46d160":"#4a8a4a",cursor:"pointer",fontSize:"12px",fontWeight:"500"}}>
                    {archivePanelOpen ? "▲ Filters" : "▼ Filters"}
                  </button>
                  <button
                    onClick={runArchiveAll}
                    disabled={!!archiveJob}
                    style={{padding:"6px 16px",background:archiveJob?"#2a2a2a":"linear-gradient(135deg,#46d160,#2ea84e)",border:"none",borderRadius:"8px",color:archiveJob?"#555":"#000",cursor:archiveJob?"not-allowed":"pointer",fontSize:"12px",fontWeight:"700",transition:"background 0.2s, color 0.2s, opacity 0.2s"}}>
                    {archiveJob ? "⏳ Archiving…" : "Archive All Posts"}
                  </button>
                </div>
              </div>

              {/* Archive progress overview */}
              {archiveStats && (()=>{
                const total = archiveStats.total_posts
                const archived = archiveStats.total_archived
                const unarchived = archiveStats.total_unarchived
                const pct = archiveStats.archive_pct
                return (
                  <div style={{background:"linear-gradient(145deg,#131f13,#0e180e)",borderRadius:"16px",border:"1px solid #1a3a1a",padding:"20px",marginBottom:"16px"}}>
                    {/* Top stats row */}
                    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",gap:"16px",marginBottom:"16px"}}>
                      {[
                        {label:"Total Posts", value:total, color:"#fff"},
                        {label:"Archived (Hidden)", value:archived, color:"#46d160"},
                        {label:"Unarchived (Active)", value:unarchived, color:unarchived===0?"#46d160":"#f9c300"},
                      ].map(s=>(
                        <div key={s.label}>
                          <div style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px",marginBottom:"4px"}}>{s.label}</div>
                          <div style={{fontSize:"26px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{s.value.toLocaleString()}</div>
                        </div>
                      ))}
                    </div>
                    {/* Progress bar */}
                    <div>
                      <div style={{display:"flex",justifyContent:"space-between",fontSize:"11px",color:"#555",marginBottom:"6px"}}>
                        <span>Archive completion</span>
                        <span style={{color:pct>=100?"#46d160":pct>50?"#7ab3e0":"#f9c300",fontWeight:"600"}}>{pct}%</span>
                      </div>
                      <div style={{background:"#111",height:"10px",borderRadius:"5px",overflow:"hidden"}}>
                        <div style={{width:`${pct}%`,background:pct>=100?"linear-gradient(90deg,#46d160,#2ea84e)":"linear-gradient(90deg,#2ea84e,#46d160)",height:"100%",borderRadius:"5px",transition:"width 0.5s ease"}}/>
                      </div>
                    </div>
                    {/* Age breakdown */}
                    {archiveStats.by_age && unarchived > 0 && (
                      <div style={{marginTop:"14px",paddingTop:"14px",borderTop:"1px solid #1a2e1a"}}>
                        <div style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px",marginBottom:"8px"}}>Unarchived posts by age</div>
                        <div style={{display:"flex",gap:"8px",flexWrap:"wrap"}}>
                          {[
                            {label:">1 year", value:archiveStats.by_age.older_1y, days:365},
                            {label:"6m–1y",   value:archiveStats.by_age.age_6m_1y, days:180},
                            {label:"3m–6m",   value:archiveStats.by_age.age_3m_6m, days:90},
                            {label:"1m–3m",   value:archiveStats.by_age.age_1m_3m, days:30},
                            {label:"<1 month",value:archiveStats.by_age.newer_1m,  days:0},
                          ].filter(b=>b.value>0).map(b=>(
                            <button
                              key={b.label}
                              onClick={()=>{
                                if(b.days===0){ toastSuccess("No quick filter for <1 month"); return }
                                if(!window.confirm(`Archive ${b.value.toLocaleString()} posts older than ${b.days} days?`)) return
                                setArchiveJobResult(null)
                                axios.post(`/api/admin/archive/bulk?before_days=${b.days}`)
                                  .then(r=>{
                                    if(!r.data.job_id){ toastSuccess(r.data.message||"Nothing to archive"); return }
                                    setArchiveJob({status:"pending",total:r.data.total,done:0,skipped:0,files_moved:0,errors:[]})
                                    startArchiveJobPoll(r.data.job_id)
                                    toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts…`)
                                  })
                                  .catch(err=>toastError("Failed: "+(err.response?.data?.detail||err.message)))
                              }}
                              disabled={!!archiveJob || b.days===0}
                              title={b.days>0?`Archive all posts older than ${b.days} days`:"Too recent to quick-archive"}
                              style={{padding:"4px 12px",background:b.days===0?"#111":"#0e200e",border:`1px solid ${b.days===0?"#1a1a1a":"#1a4a1a"}`,borderRadius:"20px",color:b.days===0?"#333":"#46d160",cursor:b.days===0||archiveJob?"not-allowed":"pointer",fontSize:"11px",fontWeight:"500",transition:"background 0.15s, color 0.15s, opacity 0.15s",opacity:archiveJob?0.5:1}}>
                              {b.label}: <b>{b.value.toLocaleString()}</b>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Active archive job progress */}
              {archiveJob && (()=>{
                const pct = archiveJob.total>0 ? Math.round(archiveJob.done/archiveJob.total*100) : 0
                return (
                  <div style={{background:"#131f13",borderRadius:"12px",border:"1px solid #1a3a1a",padding:"16px",marginBottom:"16px"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"10px"}}>
                      <span style={{fontSize:"13px",color:"#46d160",fontWeight:"600"}}>Archiving in progress…</span>
                      <span style={{fontSize:"12px",color:"#666",fontVariantNumeric:"tabular-nums"}}>{archiveJob.done.toLocaleString()} / {archiveJob.total.toLocaleString()} posts</span>
                    </div>
                    <div style={{background:"#0a120a",height:"8px",borderRadius:"4px",overflow:"hidden",marginBottom:"8px"}}>
                      <div style={{width:`${pct}%`,background:"linear-gradient(90deg,#46d160,#2ea84e)",height:"100%",borderRadius:"4px",transition:"width 0.4s ease"}}/>
                    </div>
                    <div style={{display:"flex",gap:"16px",fontSize:"11px",color:"#555"}}>
                      <span>{pct}% complete</span>
                      {archiveJob.files_moved>0 && <span style={{color:"#46d160"}}>{archiveJob.files_moved.toLocaleString()} files moved</span>}
                      {archiveJob.skipped>0 && <span style={{color:"#f9c300"}}>{archiveJob.skipped} skipped</span>}
                      {archiveJob.errors?.length>0 && <span style={{color:"#ff6b6b"}}>{archiveJob.errors.length} error(s)</span>}
                    </div>
                  </div>
                )
              })()}

              {/* Last job result */}
              {archiveJobResult && !archiveJob && (
                <div style={{background:"#0d1f0d",border:"1px solid #1a3a1a",borderRadius:"10px",padding:"12px 16px",marginBottom:"16px",fontSize:"13px",color:"#46d160"}}>
                  Archive job complete — {archiveJobResult.done?.toLocaleString()} posts processed
                  {archiveJobResult.files_moved>0 && <span style={{color:"#7ab3e0"}}>, {archiveJobResult.files_moved.toLocaleString()} files moved</span>}
                  {archiveJobResult.skipped>0 && <span style={{color:"#888"}}>, {archiveJobResult.skipped} skipped</span>}
                  {archiveJobResult.errors?.length>0 && <span style={{color:"#ff6b6b"}}>, {archiveJobResult.errors.length} error(s)</span>}
                </div>
              )}

              {/* Expandable filter panel for targeted bulk archive */}
              {archivePanelOpen && (
                <div style={{background:"#141414",borderRadius:"12px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"16px"}}>
                  <div style={{fontSize:"12px",color:"#666",marginBottom:"12px",textTransform:"uppercase",letterSpacing:"0.5px",fontWeight:"500"}}>Bulk Archive by Filter</div>
                  <div style={{display:"flex",gap:"10px",flexWrap:"wrap",alignItems:"flex-end"}}>
                    <div style={{display:"flex",flexDirection:"column",gap:"4px"}}>
                      <label style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>Target type</label>
                      <select value={archiveBulkFilter.target_type} onChange={e=>setArchiveBulkFilter(f=>({...f,target_type:e.target.value,target_name:""}))}
                        style={{padding:"8px 10px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#ccc",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                        <option value="">Any</option>
                        <option value="subreddit">r/ subreddit</option>
                        <option value="user">u/ user</option>
                      </select>
                    </div>
                    {archiveBulkFilter.target_type && (
                      <div style={{display:"flex",flexDirection:"column",gap:"4px"}}>
                        <label style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>{archiveBulkFilter.target_type==="subreddit"?"Subreddit name":"Username"}</label>
                        <input type="text"
                          placeholder={archiveBulkFilter.target_type==="subreddit"?"e.g. python":"e.g. spez"}
                          value={archiveBulkFilter.target_name}
                          onChange={e=>setArchiveBulkFilter(f=>({...f,target_name:e.target.value}))}
                          style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"160px"}}/>
                      </div>
                    )}
                    <div style={{display:"flex",flexDirection:"column",gap:"4px"}}>
                      <label style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>Older than (days)</label>
                      <input type="number" min="1" placeholder="e.g. 90"
                        value={archiveBulkFilter.before_days}
                        onChange={e=>setArchiveBulkFilter(f=>({...f,before_days:e.target.value}))}
                        style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"120px"}}/>
                    </div>
                    <div style={{display:"flex",flexDirection:"column",gap:"4px"}}>
                      <label style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>Media status</label>
                      <select value={archiveBulkFilter.media_status} onChange={e=>setArchiveBulkFilter(f=>({...f,media_status:e.target.value}))}
                        style={{padding:"8px 10px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#ccc",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                        <option value="">Any</option>
                        <option value="done">Downloaded only</option>
                        <option value="none">No media (text posts)</option>
                      </select>
                    </div>
                    <button
                      onClick={runBulkArchiveFiltered}
                      disabled={!!archiveJob}
                      style={{padding:"8px 18px",background:archiveJob?"#2a2a2a":"linear-gradient(135deg,#46d160,#2ea84e)",border:"none",borderRadius:"8px",color:archiveJob?"#555":"#000",cursor:archiveJob?"not-allowed":"pointer",fontSize:"13px",fontWeight:"700",alignSelf:"flex-end",transition:"background 0.2s, color 0.2s, opacity 0.2s"}}>
                      Archive Matching
                    </button>
                    <button
                      onClick={()=>{
                        const f = archiveBulkFilter
                        const params = new URLSearchParams()
                        if(f.target_type && f.target_name){ params.set("target_type", f.target_type); params.set("target_name", f.target_name) }
                        if(f.before_days) params.set("before_days", f.before_days)
                        if(f.media_status) params.set("media_status", f.media_status)
                        axios.post(`/api/admin/archive/bulk?${params.toString()}&dry_run=true`)
                          .then(r=>toastSuccess(`${r.data.post_count.toLocaleString()} posts would be archived`))
                          .catch(err=>toastError("Preview failed: "+(err.response?.data?.detail||err.message)))
                      }}
                      style={{padding:"8px 14px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"13px",alignSelf:"flex-end"}}>
                      Preview Count
                    </button>
                  </div>
                  {/* Per-subreddit quick targets */}
                  {archiveStats?.by_subreddit?.length > 0 && (
                    <div style={{marginTop:"14px",paddingTop:"14px",borderTop:"1px solid #222"}}>
                      <div style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px",marginBottom:"8px"}}>Quick: archive by subreddit</div>
                      <div style={{display:"flex",gap:"6px",flexWrap:"wrap"}}>
                        {archiveStats.by_subreddit.slice(0,15).map(s=>(
                          <button key={s.name}
                            onClick={()=>{
                              if(!window.confirm(`Archive all ${s.count.toLocaleString()} unarchived posts from r/${s.name}?`)) return
                              setArchiveJobResult(null)
                              axios.post(`/api/admin/target/subreddit/${encodeURIComponent(s.name)}/archive-all`)
                                .then(r=>{
                                  if(!r.data.job_id){toastSuccess(r.data.message||"Nothing to archive");return}
                                  setArchiveJob({status:"pending",total:r.data.total,done:0,skipped:0,files_moved:0,errors:[]})
                                  startArchiveJobPoll(r.data.job_id)
                                  toastSuccess(`Archiving ${r.data.total.toLocaleString()} posts…`)
                                })
                                .catch(err=>toastError("Failed: "+(err.response?.data?.detail||err.message)))
                            }}
                            disabled={!!archiveJob}
                            style={{padding:"4px 10px",background:"#111",border:"1px solid #2a2a2a",borderRadius:"20px",color:"#888",cursor:archiveJob?"not-allowed":"pointer",fontSize:"11px",transition:"background 0.15s, color 0.15s, opacity 0.15s",opacity:archiveJob?0.5:1}}>
                            r/{s.name} <span style={{color:"#46d160",fontWeight:"600"}}>{s.count.toLocaleString()}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Scrape Targets */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:"12px",marginBottom:"16px",flexWrap:"wrap"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Scrape Targets</h2>
                <button onClick={scrapeNow}
                  style={{padding:"6px 14px",background:scrapeTriggered?"#46d160":"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"8px",color:scrapeTriggered?"#000":"#fff",cursor:"pointer",fontSize:"12px",fontWeight:"600",transition:"background 0.3s ease, color 0.3s ease"}}>
                  {scrapeTriggered ? "✓ Triggered" : "⚡ Scrape Now"}
                </button>
                <button onClick={triggerBackfill}
                  style={{padding:"6px 14px",background:backfillTriggered?"#46d160":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"8px",color:backfillTriggered?"#000":"#7ab3e0",cursor:"pointer",fontSize:"12px",fontWeight:"600",transition:"background 0.3s ease, color 0.3s ease"}}>
                  {backfillTriggered ? "✓ Triggered" : "📜 Backfill"}
                </button>
              </div>
              {/* Backfill Status Display */}
              {backfillStatus && backfillStatus.status !== "none" && (
                <div style={{marginBottom:"16px",padding:"12px",background:backfillStatus.status==="done"?"#0d2818":backfillStatus.status==="partial"?"#2d2000":"#1e3a5f",borderRadius:"8px",border:`1px solid ${backfillStatus.status==="done"?"#46d160":backfillStatus.status==="partial"?"#f9c300":"#2a5a8a"}`}}>
                  <div style={{fontSize:"13px",fontWeight:"600",color:backfillStatus.status==="done"?"#46d160":backfillStatus.status==="partial"?"#f9c300":"#7ab3e0",marginBottom:"8px"}}>
                    {backfillStatus.status === "done" ? "✓ Backfill Complete" : backfillStatus.status === "partial" ? "⚠ Backfill Partial" : "🔄 Backfill Running…"}
                  </div>
                  <div style={{display:"flex",gap:"16px",fontSize:"12px",color:"#ccc",marginBottom:"8px",flexWrap:"wrap"}}>
                    <span>Total: <b style={{color:"#fff"}}>{backfillStatus.total}</b></span>
                    <span>New: <b style={{color:"#46d160"}}>{backfillStatus.new}</b></span>
                    <span>Skipped: <b style={{color:"#888"}}>{backfillStatus.skipped}</b></span>
                    <span>Completed: <b style={{color:"#fff"}}>{backfillStatus.completed}</b>/{backfillStatus.targets_total}</span>
                    {backfillStatus.rate_limited > 0 && (
                      <span style={{color:"#f9c300"}}>Rate Limited: <b>{backfillStatus.rate_limited}</b></span>
                    )}
                  </div>
                  {backfillStatus.errors && backfillStatus.errors.length > 0 && (
                    <div style={{fontSize:"11px",color:"#ff6a33",background:"#1a0a00",padding:"8px",borderRadius:"4px",maxHeight:"100px",overflowY:"auto"}}>
                      <div style={{fontWeight:"600",marginBottom:"4px",color:"#ff4500"}}>Errors:</div>
                      {backfillStatus.errors.map((e,i)=><div key={i} style={{fontFamily:"monospace",marginBottom:"2px"}}>{e}</div>)}
                    </div>
                  )}
                </div>
              )}
              {/* Add target form */}
              <div style={{display:"flex",gap:"8px",alignItems:"center"}}>
                <select value={addTargetType} onChange={e=>setAddTargetType(e.target.value)}
                   aria-label="Target type"
                   style={{padding:"8px 10px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#ccc",fontSize:"13px",cursor:"pointer"}}>
                   <option value="subreddit">r/ subreddit</option>
                   <option value="user">u/ user</option>
                 </select>
                 <input type="text" placeholder="name…" aria-label={`Add ${addTargetType} name`} autoComplete="off" spellCheck={false} value={addTargetName} onChange={e=>setAddTargetName(e.target.value)}
                   onKeyDown={e=>e.key==="Enter"&&addTarget()}
                   style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"160px"}}/>
                <button onClick={addTarget} disabled={!addTargetName.trim()}
                  style={{padding:"8px 16px",background:addTargetName.trim()?"linear-gradient(135deg,#ff4500,#ff6a33)":"#2a2a2a",border:"none",borderRadius:"8px",color:addTargetName.trim()?"#fff":"#555",cursor:addTargetName.trim()?"pointer":"not-allowed",fontSize:"13px",fontWeight:"600"}}>
                  + Add
                </button>
              </div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"8px",marginBottom:"40px"}}>
              {adminData.targets && adminData.targets.map(t=>{
                const cardKey = `${t.type}:${t.name}`
                const isExpanded = expandedCard === cardKey
                const audit = cardAudit[cardKey]
                const auditLoading = cardAuditLoading[cardKey]
                const isScraping = cardScraping[cardKey]
                const isBackfilling = cardBackfilling[cardKey]
                return (
                <div key={`${t.type}-${t.name}`} style={{
                  background:"linear-gradient(145deg,#1e1e1e,#171717)",
                  borderRadius:"10px",
                  border:t.status==="taken_down"?"1px solid #ff000044":t.status==="deleted"?"1px solid #ffff00044":isExpanded?"1px solid #ff450055":"1px solid #2a2a2a",
                  opacity:t.enabled?1:0.7,
                  transition:"background 0.2s ease, color 0.2s ease, transform 0.2s ease",
                  display:"flex",
                  flexDirection:"column",
                  overflow:"hidden",
                }}>
                  {/* Card header — click to expand */}
                  <div style={{padding:"12px",display:"flex",flexDirection:"column",gap:"8px"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                      <div style={{minWidth:0,flex:1,cursor:"pointer"}} onClick={()=>toggleCardExpand(t.type,t.name)}>
                        <div style={{display:"flex",alignItems:"center",gap:"6px"}}>
                          <span style={{fontSize:"9px",color:"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>{t.type}</span>
                          {t.status!=="active" && (
                            <span style={{fontSize:"8px",padding:"1px 4px",borderRadius:"3px",background:t.status==="taken_down"?"#440000":t.status==="deleted"?"#444400":"#222",color:t.status==="taken_down"?"#ff4444":t.status==="deleted"?"#ffff44":"#888"}}>
                              {t.status==="taken_down"?"⛔":t.status==="deleted"?"👤":""}
                            </span>
                          )}
                        </div>
                        <div style={{fontSize:"14px",fontWeight:"600",color:t.status==="active"?"#fff":t.status==="taken_down"?"#ff6666":t.status==="deleted"?"#ffff66":"#888",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                          {t.type==="subreddit"?"r/":"u/"}{t.name}
                        </div>
                      </div>
                      <div style={{display:"flex",gap:"4px",flexShrink:0,alignItems:"center"}}>
                        {t.status==="active" && (
                          <button onClick={()=>toggleTarget(t.type,t.name)} title={t.enabled?"Disable scraping":"Enable scraping"} style={{padding:"3px 8px",background:t.enabled?"#46d160":"#3a3a3a",border:"none",borderRadius:"6px",color:t.enabled?"#000":"#888",cursor:"pointer",fontSize:"10px",fontWeight:"600"}}>
                            {t.enabled?"●":"○"}
                          </button>
                        )}
                        {(t.status==="taken_down"||t.status==="deleted") && (
                          <button onClick={()=>setTargetStatus(t.type,t.name,"active")} title="Reactivate" style={{padding:"3px 8px",background:"#003300",border:"1px solid #00aa00",borderRadius:"6px",color:"#44ff44",cursor:"pointer",fontSize:"10px"}}>♻</button>
                        )}
                        {t.status==="active" && (
                          <>
                            {t.type==="subreddit" && (
                              <button onClick={()=>setTargetStatus(t.type,t.name,"taken_down")} title="Mark taken down" style={{padding:"3px 6px",background:"#2a0000",border:"1px solid #550000",borderRadius:"6px",color:"#ff4444",cursor:"pointer",fontSize:"10px"}}>⛔</button>
                            )}
                            {t.type==="user" && (
                              <button onClick={()=>setTargetStatus(t.type,t.name,"deleted")} title="Mark deleted" style={{padding:"3px 6px",background:"#2a2a00",border:"1px solid #555500",borderRadius:"6px",color:"#ffff44",cursor:"pointer",fontSize:"10px"}}>👤</button>
                            )}
                            <button onClick={()=>deleteTarget(t.type,t.name)} title="Remove target" style={{padding:"3px 6px",background:"#2a0000",border:"1px solid #440000",borderRadius:"6px",color:"#ff4444",cursor:"pointer",fontSize:"10px"}}>✕</button>
                          </>
                        )}
                        {/* Expand/collapse chevron */}
                        <button onClick={()=>toggleCardExpand(t.type,t.name)} title={isExpanded?"Collapse":"Expand actions"} style={{padding:"3px 6px",background:"transparent",border:"1px solid #333",borderRadius:"6px",color:isExpanded?"#ff4500":"#555",cursor:"pointer",fontSize:"12px",lineHeight:1,transition:"color 0.2s"}}>
                          {isExpanded?"▲":"▼"}
                        </button>
                      </div>
                    </div>
                    {(t.status==="active" || t.status==="taken_down") && (
                      <div style={{display:"flex",gap:"8px",fontSize:"11px"}}>
                        <div style={{color:"#666"}}>Posts:</div>
                        <div style={{color:t.status==="taken_down"?"#888":"#fff",fontVariantNumeric:"tabular-nums"}}>{t.post_count?.toLocaleString()}</div>
                        <div style={{color:"#666"}}>Media:</div>
                        <div style={{color:"#46d160",fontVariantNumeric:"tabular-nums"}}>{t.downloaded_media}/{t.total_media}</div>
                      </div>
                    )}
                    {t.status==="active" && (
                      <>
                        <div style={{background:"#141414",height:"4px",borderRadius:"2px",overflow:"hidden"}}>
                          <div style={{width:`${Math.min(100,t.progress_percent)}%`,background:t.progress_percent>=100?"#46d160":"linear-gradient(90deg,#ff4500,#ff6a33)",height:"100%",borderRadius:"2px"}}/>
                        </div>
                        {t.last_created && (
                          <div style={{fontSize:"10px",color:"#444",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
                            {new Date(t.last_created).toLocaleDateString()} · {formatRate(t.rate_per_second)}
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {/* Expanded panel */}
                  {isExpanded && (()=>{
                    const isArchivingTarget = cardArchiving[cardKey]
                    return (
                    <div style={{borderTop:"1px solid #2a2a2a",padding:"10px 12px",background:"#161616",display:"flex",flexDirection:"column",gap:"10px"}}>

                      {/* Action buttons row */}
                      <div style={{display:"flex",gap:"6px",flexWrap:"wrap"}}>
                        {t.status==="active" && t.enabled && (
                          <button
                            onClick={()=>scrapeTargetNow(t.type,t.name)}
                            disabled={isScraping}
                            title="Trigger an immediate scrape for this target only"
                            style={{display:"flex",alignItems:"center",gap:"4px",padding:"5px 10px",background:isScraping?"#2a2a2a":"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"6px",color:isScraping?"#555":"#fff",cursor:isScraping?"not-allowed":"pointer",fontSize:"11px",fontWeight:"600",transition:"background 0.2s, color 0.2s, opacity 0.2s",flex:"1",justifyContent:"center",minWidth:"80px"}}>
                            {isScraping ? "✓ Sent" : "⚡ Scrape"}
                          </button>
                        )}
                        {t.status==="active" && t.enabled && (
                          <button
                            onClick={()=>backfillTargetNow(t.type,t.name)}
                            disabled={isBackfilling}
                            title="Trigger a historical backfill for this target only"
                            style={{display:"flex",alignItems:"center",gap:"4px",padding:"5px 10px",background:isBackfilling?"#2a2a2a":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"6px",color:isBackfilling?"#555":"#7ab3e0",cursor:isBackfilling?"not-allowed":"pointer",fontSize:"11px",fontWeight:"600",transition:"background 0.2s, color 0.2s, opacity 0.2s",flex:"1",justifyContent:"center",minWidth:"80px"}}>
                            {isBackfilling ? "✓ Sent" : "📜 Backfill"}
                          </button>
                        )}
                        <button
                          onClick={()=>rescanTarget(t.type,t.name)}
                          title="Re-queue all media for this target for re-download"
                          style={{display:"flex",alignItems:"center",gap:"4px",padding:"5px 10px",background:"#1e2a1e",border:"1px solid #2a4a2a",borderRadius:"6px",color:"#46d160",cursor:"pointer",fontSize:"11px",fontWeight:"600",flex:"1",justifyContent:"center",minWidth:"80px"}}>
                          ↻ Rescan
                        </button>
                        <button
                          onClick={()=>fetchCardAudit(t.type,t.name)}
                          disabled={auditLoading}
                          title="Check media integrity for this target"
                          style={{display:"flex",alignItems:"center",gap:"4px",padding:"5px 10px",background:"#1e1e2a",border:"1px solid #2a2a4a",borderRadius:"6px",color:auditLoading?"#555":"#7193ff",cursor:auditLoading?"not-allowed":"pointer",fontSize:"11px",fontWeight:"600",flex:"1",justifyContent:"center",minWidth:"80px"}}>
                          {auditLoading ? "…" : "🔍 Audit"}
                        </button>
                        <button
                          onClick={()=>{
                            if(!window.confirm(`Archive all ${t.post_count?.toLocaleString()||"?"} posts from ${t.type==="subreddit"?"r/":"u/"}${t.name}?\n\nThis will move their media files to the archive directory and hide them from the Browse tab.`)) return
                            runArchiveTarget(t.type, t.name)
                          }}
                          disabled={!!archiveJob||isArchivingTarget||t.post_count===0}
                          title={`Archive all posts from ${t.type==="subreddit"?"r/":"u/"}${t.name}`}
                          style={{display:"flex",alignItems:"center",gap:"4px",padding:"5px 10px",background:isArchivingTarget?"#2a2a2a":"#132213",border:"1px solid #1a3a1a",borderRadius:"6px",color:isArchivingTarget?"#555":"#46d160",cursor:(archiveJob||isArchivingTarget||t.post_count===0)?"not-allowed":"pointer",fontSize:"11px",fontWeight:"600",transition:"background 0.2s, color 0.2s, opacity 0.2s",flex:"1",justifyContent:"center",minWidth:"80px"}}>
                          {isArchivingTarget ? "⏳" : "📦 Archive All"}
                        </button>
                      </div>

                      {/* Audit results */}
                      {auditLoading && (
                        <div style={{fontSize:"11px",color:"#555",textAlign:"center",padding:"8px 0"}}>Running integrity check…</div>
                      )}
                      {audit && !auditLoading && (()=>{
                        const missingPct = audit.total_media > 0 ? Math.round((audit.media_missing / audit.total_media) * 100) : 0
                        const okPct = audit.total_media > 0 ? Math.round((audit.media_ok / audit.total_media) * 100) : 100
                        const hasIssues = audit.media_missing > 0 || audit.posts_all_missing > 0
                        return (
                          <div style={{background:"#111",borderRadius:"8px",padding:"10px",border:`1px solid ${hasIssues?"#ff450033":"#46d16033"}`}}>
                            <div style={{fontSize:"10px",fontWeight:"600",color:hasIssues?"#ff6b3d":"#46d160",marginBottom:"8px",textTransform:"uppercase",letterSpacing:"0.5px"}}>
                              {hasIssues ? "⚠ Issues Found" : "✓ Integrity OK"}
                            </div>
                            {/* Media bar */}
                            <div style={{marginBottom:"8px"}}>
                              <div style={{display:"flex",justifyContent:"space-between",fontSize:"10px",color:"#666",marginBottom:"3px"}}>
                                <span>Media integrity</span>
                                <span style={{color:hasIssues?"#f9c300":"#46d160"}}>{okPct}% OK</span>
                              </div>
                              <div style={{background:"#222",height:"5px",borderRadius:"3px",overflow:"hidden"}}>
                                <div style={{display:"flex",height:"100%"}}>
                                  <div style={{width:`${okPct}%`,background:"#46d160",transition:"width 0.4s"}}/>
                                  {missingPct>0 && <div style={{width:`${missingPct}%`,background:"#ff4500",transition:"width 0.4s"}}/>}
                                </div>
                              </div>
                            </div>
                            {/* Stats grid */}
                            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"4px",fontSize:"10px"}}>
                              <div style={{color:"#666"}}>Posts total</div><div style={{color:"#fff",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.total_posts.toLocaleString()}</div>
                              <div style={{color:"#46d160"}}>Posts OK</div><div style={{color:"#46d160",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.posts_ok.toLocaleString()}</div>
                              {audit.posts_partial>0 && <><div style={{color:"#f9c300"}}>Posts partial</div><div style={{color:"#f9c300",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.posts_partial.toLocaleString()}</div></>}
                              {audit.posts_all_missing>0 && <><div style={{color:"#ff6b6b"}}>Posts all missing</div><div style={{color:"#ff6b6b",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.posts_all_missing.toLocaleString()}</div></>}
                              {audit.posts_no_media>0 && <><div style={{color:"#888"}}>Posts (no media)</div><div style={{color:"#888",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.posts_no_media.toLocaleString()}</div></>}
                              <div style={{color:"#666",marginTop:"4px",borderTop:"1px solid #222",paddingTop:"4px"}}>Media total</div><div style={{color:"#fff",textAlign:"right",fontVariantNumeric:"tabular-nums",marginTop:"4px",borderTop:"1px solid #222",paddingTop:"4px"}}>{audit.total_media.toLocaleString()}</div>
                              <div style={{color:"#46d160"}}>Downloaded</div><div style={{color:"#46d160",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.media_ok.toLocaleString()}</div>
                              {audit.media_missing>0 && <><div style={{color:"#ff6b6b"}}>Missing</div><div style={{color:"#ff6b6b",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.media_missing.toLocaleString()}</div></>}
                              {audit.media_error>0 && <><div style={{color:"#ff4500"}}>Errors</div><div style={{color:"#ff4500",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.media_error.toLocaleString()}</div></>}
                              {audit.media_pending>0 && <><div style={{color:"#f9c300"}}>Pending</div><div style={{color:"#f9c300",textAlign:"right",fontVariantNumeric:"tabular-nums"}}>{audit.media_pending.toLocaleString()}</div></>}
                            </div>
                            {/* Quick fix: if missing media, offer rescan */}
                            {audit.media_missing>0 && (
                              <button
                                onClick={()=>rescanTarget(t.type,t.name)}
                                style={{marginTop:"8px",width:"100%",padding:"5px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"6px",color:"#fff",cursor:"pointer",fontSize:"11px",fontWeight:"600"}}>
                                Re-queue {audit.media_missing.toLocaleString()} missing →
                              </button>
                            )}
                          </div>
                        )
                      })()}
                    </div>
                  )})()}
                </div>
              )})}
            </div>

            {/* ── Thumbnail Utilities ── */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:"12px",marginBottom:"16px",flexWrap:"wrap"}}>
              <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
                <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Thumbnail Utilities</h2>
              </div>
              <button onClick={loadThumbStats} style={{padding:"6px 14px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#888",cursor:"pointer",fontSize:"12px"}}>↻ Refresh Stats</button>
            </div>

            {/* Stats row */}
            {thumbStats && (
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(160px,1fr))",gap:"12px",marginBottom:"20px"}}>
                {[
                  {label:"Media with file",value:thumbStats.total_media_with_file,color:"#fff"},
                  {label:"Thumbs OK",value:thumbStats.with_thumb_in_db,color:"#46d160"},
                  {label:"Missing thumbs",value:thumbStats.missing_thumb_in_db,color:thumbStats.missing_thumb_in_db>0?"#f9c300":"#46d160",
                   sub:thumbStats.missing_thumb_in_db>0?`${thumbStats.missing_no_db_path} no path · ${thumbStats.missing_file_gone} file gone`:null},
                  {label:"Files on disk",value:thumbStats.thumb_files_on_disk,color:"#7193ff"},
                  {label:"Disk usage",value:`${thumbStats.thumb_disk_mb} MB`,color:"#888"},
                ].map(s=>(
                  <div key={s.label} style={{background:"#1a1a1a",padding:"14px 16px",borderRadius:"12px",border:"1px solid #2a2a2a"}}>
                    <div style={{fontSize:"11px",color:"#555",marginBottom:"6px",textTransform:"uppercase",letterSpacing:"0.5px"}}>{s.label}</div>
                    <div style={{fontSize:"22px",fontWeight:"700",color:s.color,fontVariantNumeric:"tabular-nums"}}>{typeof s.value==="number"?s.value.toLocaleString():s.value}</div>
                    {s.sub && <div style={{fontSize:"10px",color:"#666",marginTop:"4px"}}>{s.sub}</div>}
                  </div>
                ))}
              </div>
            )}

            {/* Action buttons */}
            <div style={{display:"flex",gap:"10px",flexWrap:"wrap",marginBottom:"16px"}}>
              <button
                onClick={runThumbBackfill}
                disabled={!!thumbJob}
                style={{padding:"10px 20px",background:thumbJob?"#2a2a2a":"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"10px",color:thumbJob?"#555":"#fff",cursor:thumbJob?"not-allowed":"pointer",fontSize:"13px",fontWeight:"600"}}>
                Backfill Missing
              </button>
              <button
                onClick={runThumbRebuildAll}
                disabled={!!thumbJob}
                style={{padding:"10px 20px",background:thumbJob?"#2a2a2a":"#1e3a5f",border:"1px solid #2a5a8a",borderRadius:"10px",color:thumbJob?"#555":"#7ab3e0",cursor:thumbJob?"not-allowed":"pointer",fontSize:"13px",fontWeight:"600"}}>
                Rebuild All
              </button>
              <button
                onClick={runThumbPurgeOrphans}
                disabled={!!thumbJob}
                style={{padding:"10px 20px",background:thumbJob?"#2a2a2a":"#2a0000",border:"1px solid #550000",borderRadius:"10px",color:thumbJob?"#555":"#ff6b6b",cursor:thumbJob?"not-allowed":"pointer",fontSize:"13px",fontWeight:"600"}}>
                Purge Orphans
              </button>
            </div>

            {/* Job progress bar */}
            {thumbJob && (()=>{
              const pct = thumbJob.total>0 ? Math.round(thumbJob.done/thumbJob.total*100) : 0
              return (
                <div style={{background:"#1a1a1a",borderRadius:"12px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"20px"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"10px"}}>
                    <span style={{fontSize:"13px",color:"#ccc",fontWeight:"500",textTransform:"capitalize"}}>{thumbJob.type||"job"} — {thumbJob.status}</span>
                    <span style={{fontSize:"12px",color:"#666",fontVariantNumeric:"tabular-nums"}}>{thumbJob.done.toLocaleString()} / {thumbJob.total.toLocaleString()}</span>
                  </div>
                  <div style={{background:"#141414",height:"8px",borderRadius:"4px",overflow:"hidden",marginBottom:"6px"}}>
                    <div style={{width:`${pct}%`,background:"linear-gradient(90deg,#ff4500,#ff6a33)",height:"100%",borderRadius:"4px",transition:"width 0.4s ease"}}/>
                  </div>
                  <div style={{display:"flex",justifyContent:"space-between",fontSize:"11px",color:"#555"}}>
                    <span>{pct}%{thumbJob.skipped>0?` · ${thumbJob.skipped} skipped`:""}</span>
                    {thumbJob.errors?.length>0 && <span style={{color:"#ff6b6b"}}>{thumbJob.errors.length} error(s)</span>}
                  </div>
                </div>
              )
            })()}

            {/* Last job result summary */}
            {thumbJobResult && !thumbJob && (
              <div style={{background:"#0d1f0d",border:"1px solid #1a3a1a",borderRadius:"10px",padding:"12px 16px",marginBottom:"20px",fontSize:"13px",color:"#46d160"}}>
                Job complete — {thumbJobResult.done?.toLocaleString()} processed
                {thumbJobResult.skipped>0 && <span style={{color:"#888"}}>, {thumbJobResult.skipped} skipped</span>}
                {thumbJobResult.errors?.length>0 && <span style={{color:"#ff6b6b"}}>, {thumbJobResult.errors.length} error(s)</span>}
              </div>
            )}

            {/* ── Media Re-scan Utility ── */}
            <div style={{display:"flex",alignItems:"center",gap:"12px",marginBottom:"16px",marginTop:"32px"}}>
              <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#ff4500,#ff6a33)",borderRadius:"2px"}} />
              <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Media Re-scan</h2>
            </div>
            <div style={{background:"#1a1a1a",borderRadius:"12px",border:"1px solid #2a2a2a",padding:"16px",marginBottom:"20px"}}>
              <p style={{fontSize:"13px",color:"#888",marginBottom:"12px",margin:0}}>
                Re-scan existing posts to find additional images/videos that weren't originally queued for download.
                Useful for retroactively capturing gallery images or fixing posts archived before full media extraction was implemented.
              </p>
              <button
                onClick={()=>{
                  if(!window.confirm("Re-scan ALL posts for missing media? This may queue many items.")) return
                  axios.post("/api/admin/media/rescan").then(r=>{
                    toastSuccess(`Scanned ${r.data.posts_scanned} posts, found ${r.data.urls_found} URLs, queued ${r.data.newly_queued} new items`)
                    loadAdmin()
                  }).catch(err=>toastError("Rescan failed: " + (err.response?.data?.detail||err.message)))
                }}
                style={{padding:"10px 20px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"10px",color:"#fff",cursor:"pointer",fontSize:"13px",fontWeight:"600"}}>
                Re-scan All Posts
              </button>
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
          <button onClick={refreshPosts} aria-label={`${newPostsAvailable} new post${newPostsAvailable>1?"s":""} available — click to refresh`} style={{position:"sticky",top:"73px",zIndex:90,width:"100%",margin:"0",padding:"12px 24px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",color:"#fff",textAlign:"center",cursor:"pointer",fontSize:"14px",fontWeight:"600",boxShadow:"0 4px 20px rgba(255,69,0,0.4)",transition:"opacity 0.2s ease",letterSpacing:"0.3px",border:"none"}}>
            ↑ {newPostsAvailable} new post{newPostsAvailable>1?"s":""} — click to refresh
          </button>
        )}

        {/* ── FILTER / SORT BAR ── */}
        {!searchResults && (
          <div style={{borderBottom:"1px solid #1e1e1e",background:"#111"}}>
            {/* Mobile filter toggle bar */}
            <div style={{padding:"8px 16px",display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                <button
                  onClick={()=>setFilterBarOpen(o=>!o)}
                  aria-label={filterBarOpen?"Collapse filters":"Expand filters"}
                  aria-expanded={filterBarOpen}
                  style={{display:"flex",alignItems:"center",gap:"6px",padding:"8px 14px",background:filterBarOpen||hasActiveFilters()?"#ff450018":"#1a1a1a",border:`1px solid ${filterBarOpen||hasActiveFilters()?"#ff450044":"#2a2a2a"}`,borderRadius:"8px",color:hasActiveFilters()?"#ff6a33":"#888",cursor:"pointer",fontSize:"13px",fontWeight:"500",transition:"background 0.2s, border-color 0.2s, color 0.2s"}}>
                  <span style={{fontSize:"14px"}} aria-hidden="true">⚙</span>
                  Filters
                  {hasActiveFilters() && <span style={{background:"#ff4500",color:"#fff",borderRadius:"10px",padding:"1px 6px",fontSize:"10px",fontWeight:"700"}}>ON</span>}
                  <span style={{fontSize:"10px",opacity:0.6,marginLeft:"2px"}}>{filterBarOpen?"▲":"▼"}</span>
                </button>
                <select
                  value={sortBy}
                  onChange={e=>{
                    const v = e.target.value
                    setSortBy(v)
                    const f = {...filtersRef.current, sort: v}
                    applyFilters(f)
                  }}
                  style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:sortBy!=="last_added"?"#ff6a33":"#888",fontSize:"13px",cursor:"pointer",outline:"none"}}
                >
                  <option value="last_added">Last added</option>
                  <option value="newest">Reddit date ↓</option>
                  <option value="oldest">Reddit date ↑</option>
                  <option value="title_asc">Title A → Z</option>
                  <option value="title_desc">Title Z → A</option>
                </select>
              </div>
              {hasActiveFilters() && (
                <button onClick={clearFilters} style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #ff450044",borderRadius:"8px",color:"#ff6a33",cursor:"pointer",fontSize:"12px",fontWeight:"500",whiteSpace:"nowrap"}}>✕ Clear</button>
              )}
            </div>

            {/* Expandable filter panel */}
            {filterBarOpen && (
              <div style={{padding:"12px 16px 16px",borderTop:"1px solid #1a1a1a"}}>
                <div style={{maxWidth:"1400px",margin:"0 auto",display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
                  <input
                    type="text" inputMode="text" placeholder="r/ subreddit…"
                    aria-label="Filter by subreddit" autoComplete="off" spellCheck={false}
                    value={filterSubreddit}
                    onChange={e=>{
                      const v = e.target.value
                      setFilterSubreddit(v)
                      clearTimeout(searchTimeout._filterSubreddit)
                      searchTimeout._filterSubreddit = setTimeout(()=>{
                        applyFilters({...filtersRef.current, subreddit: v})
                      }, 400)
                    }}
                    style={{padding:"9px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}
                  />
                  <input
                    type="text" inputMode="text" placeholder="u/ author…"
                    aria-label="Filter by author" autoComplete="off" spellCheck={false}
                    value={filterAuthor}
                    onChange={e=>{
                      const v = e.target.value
                      setFilterAuthor(v)
                      clearTimeout(searchTimeout._filterAuthor)
                      searchTimeout._filterAuthor = setTimeout(()=>{
                        applyFilters({...filtersRef.current, author: v})
                      }, 400)
                    }}
                    style={{padding:"9px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}
                  />
                  <div style={{display:"flex",alignItems:"center",gap:"6px",flexWrap:"wrap"}}>
                    {[
                      {value:"image", label:"🖼 Images"},
                      {value:"video", label:"🎬 Videos"},
                      {value:"text", label:"📝 Text"},
                    ].map(mt => (
                      <label key={mt.value} style={{display:"flex",alignItems:"center",gap:"5px",cursor:"pointer",padding:"7px 10px",background:filterMediaTypes.includes(mt.value)?"#ff450022":"#1a1a1a",borderRadius:"8px",border:"1px solid",borderColor:filterMediaTypes.includes(mt.value)?"#ff4500":"#2a2a2a",transition:"background 0.15s, color 0.15s, opacity 0.15s",minHeight:"36px"}}>
                        <input type="checkbox" checked={filterMediaTypes.includes(mt.value)} onChange={e=>{
                          const newTypes = e.target.checked
                            ? [...filterMediaTypes, mt.value]
                            : filterMediaTypes.filter(t => t !== mt.value)
                          setFilterMediaTypes(newTypes)
                          applyFilters({...filtersRef.current, mediaTypes: newTypes})
                        }} style={{width:"14px",height:"14px",accentColor:"#ff4500"}}/>
                        <span style={{fontSize:"12px",color:filterMediaTypes.includes(mt.value)?"#ff6a33":"#666"}}>{mt.label}</span>
                      </label>
                    ))}
                  </div>
                  <label style={{display:"flex",alignItems:"center",gap:"6px",cursor:"pointer",minHeight:"36px",padding:"4px 0"}}>
                    <input
                      type="checkbox" checked={showNsfw}
                      onChange={e=>{
                        const v = e.target.checked
                        setShowNsfw(v)
                        localStorage.setItem("showNsfw", String(v))
                        applyFilters({...filtersRef.current, nsfw: v})
                      }}
                      style={{width:"16px",height:"16px",accentColor:"#ff4500"}}
                    />
                    <span style={{fontSize:"12px",color:showNsfw?"#ff6a33":"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>NSFW</span>
                  </label>
                </div>
              </div>
            )}
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
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"16px"}}>
              {searchResults.map(p=>(
                <div key={p.id} onClick={()=>{setGalleryIdx(0);setSelectedPost(p)}} className="post-card" style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"14px",overflow:"hidden",cursor:"pointer",border:"1px solid #2a2a2a",transition:"background 0.2s ease, color 0.2s ease, transform 0.2s ease"}}>
                  {p.is_video || p.video_url ? (
                    <div style={{aspectRatio:"1",background:"#0a0a0a",position:"relative"}}>
                      {(p.thumb_url || p.video_url) && (
                        <img src={p.thumb_url || p.video_url} loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover",opacity:0.6}} onError={e=>e.target.style.display="none"}/>
                      )}
                      <div style={{position:"absolute",inset:0,display:"flex",alignItems:"center",justifyContent:"center"}}>
                        <div style={{width:"48px",height:"48px",borderRadius:"50%",background:"rgba(0,0,0,0.6)",border:"2px solid rgba(255,69,0,0.7)",display:"flex",alignItems:"center",justifyContent:"center"}}>
                          <div style={{width:0,height:0,borderTop:"10px solid transparent",borderBottom:"10px solid transparent",borderLeft:"16px solid #ff4500",marginLeft:"4px"}}/>
                        </div>
                      </div>
                      <div style={{position:"absolute",top:"8px",left:"8px",background:"rgba(0,0,0,0.75)",borderRadius:"4px",padding:"3px 6px",fontSize:"9px",fontWeight:"700",color:"#fff",letterSpacing:"0.5px"}}>VIDEO</div>
                    </div>
                  ) : p.image_url ? (
                    <div style={{aspectRatio:"1",background:"#0a0a0a",position:"relative"}}>
                      <img src={p.image_url} loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover"}} onError={e=>e.target.style.display="none"}/>
                    </div>
                  ) : null}
                  <div style={{padding:"16px"}}>
                    <div style={{fontSize:"11px",color:"#ff4500",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"6px"}}>{p.subreddit ? `r/${p.subreddit}` : ""}</div>
                    <div style={{fontWeight:"500",marginBottom:"8px",lineHeight:"1.4",color:"#e0e0e0"}}>{p.title}</div>
                    {p.author && <div style={{fontSize:"12px",color:"#555"}}>u/{p.author}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!searchResults && (
          <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"16px"}} className="mobile-grid-2">
              {posts.map(p=>(
                <article key={p.id}
                  onClick={()=>{setGalleryIdx(0);setSelectedPost(p)}}
                  onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setGalleryIdx(0);setSelectedPost(p)}}}
                  onMouseEnter={()=>setHoveredCard(p.id)} onMouseLeave={()=>setHoveredCard(null)}
                  role="button" tabIndex={0} aria-label={p.title}
                  className="post-card"
                  style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",borderRadius:"16px",overflow:"hidden",cursor:"pointer",border:"1px solid #2a2a2a"}}>
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
                        <div style={{width:"100%",height:"100%",display:"flex",alignItems:"center",justifyContent:"center",background:"linear-gradient(135deg,#111 0%,#1a1a1a 100%)",position:"relative"}}>
                          {/* Static thumbnail behind play button */}
                          {(p.thumb_url || p.preview_url) && (
                             <img
                               src={p.thumb_url || p.preview_url}
                               alt="" loading="lazy" decoding="async"
                               style={{position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover",opacity:0.7}}
                               onError={e=>e.target.style.display="none"}
                             />
                          )}
                          <div style={{position:"relative",zIndex:1,width:"64px",height:"64px",borderRadius:"50%",background:"rgba(0,0,0,0.55)",border:"2px solid rgba(255,69,0,0.7)",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.2s ease, color 0.2s ease, transform 0.2s ease",transform:hoveredCard===p.id?"scale(1.1)":"scale(1)",backdropFilter:"blur(2px)"}}>
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
                  ) : (p.url || p.image_urls?.[0]) ? (
                    <div style={{aspectRatio:"1",background:"#141414",position:"relative",overflow:"hidden"}}>
                      <img src={p.url || p.image_urls?.[0]} alt={p.title} loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover",transition:"transform 0.3s ease"}} onError={e=>e.target.style.display="none"}/>
                      {/* Gallery indicator */}
                      {p.image_urls?.length > 1 && (
                        <div style={{position:"absolute",top:"10px",right:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"4px 10px",fontSize:"11px",fontWeight:"600",color:"#fff"}}>
                        1/{p.image_urls.length}
                      </div>
                      )}
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
                  <div style={{padding:"10px 14px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:"8px"}}>
                      <div style={{minWidth:0,flex:1}}>
                        <div style={{fontSize:"10px",color:"#666",textTransform:"uppercase",letterSpacing:"1px",marginBottom:"3px"}}>{p.subreddit||"reddit"}</div>
                        <div style={{fontSize:"13px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#ccc"}}>{p.title}</div>
                      </div>
                      <div style={{display:"flex",gap:"4px",flexShrink:0}}>
                        <button onClick={e=>{e.stopPropagation();deletePost(p.id)}} aria-label="Delete post"
                          style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#666",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                          <span aria-hidden="true">🗑</span>
                        </button>
                        <button onClick={e=>{e.stopPropagation();archivePost(p.id)}} aria-label="Hide post"
                          style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#666",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                          <span aria-hidden="true">👁</span>
                        </button>
                      </div>
                    </div>
                </article>
              ))}
            </div>
            {isLoading && (
              <div style={{padding:"40px",textAlign:"center",color:"#ff4500",fontSize:"14px"}}>
                <div style={{display:"inline-flex",alignItems:"center",gap:"8px"}}>
                  <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#ff4500",borderRadius:"50%",animation:"spin 1s linear infinite"}}/>
                  Loading posts…
                </div>
              </div>
            )}
            {!isLoading && posts.length === 0 && (
              <div style={{padding:"60px",textAlign:"center",color:"#555",fontSize:"14px"}}>
                No posts found. Try adjusting your filters.
              </div>
            )}
            <div ref={loader} style={{padding:"60px",textAlign:"center",color:"#444",fontSize:"14px"}}>
              <div style={{display:"inline-flex",alignItems:"center",gap:"8px"}}>
                <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#ff4500",borderRadius:"50%",animation:"spin 1s linear infinite"}}/>
                Loading more posts…
              </div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          </div>
        )}
      </>)}

      {/* ── HIDDEN TAB ── */}
      {activeTab === "archive" && (<>
        {/* Hidden filter/sort bar */}
        {!archiveSearchResults && (
          <div style={{borderBottom:"1px solid #1e1e1e",background:"#111"}}>
            <div style={{padding:"8px 16px",display:"flex",alignItems:"center",justifyContent:"space-between",gap:"8px",flexWrap:"wrap",maxWidth:"1400px",margin:"0 auto"}}>
              <div style={{display:"flex",alignItems:"center",gap:"8px",flexWrap:"wrap"}}>
                <button
                  onClick={()=>setArchiveFilterBarOpen(o=>!o)}
                  aria-label={archiveFilterBarOpen?"Collapse hidden filters":"Expand hidden filters"}
                  aria-expanded={archiveFilterBarOpen}
                  style={{display:"flex",alignItems:"center",gap:"6px",padding:"8px 14px",background:archiveFilterBarOpen||hasActiveArchiveFilters()?"#ff450018":"#1a1a1a",border:`1px solid ${archiveFilterBarOpen||hasActiveArchiveFilters()?"#ff450044":"#2a2a2a"}`,borderRadius:"8px",color:hasActiveArchiveFilters()?"#ff6a33":"#888",cursor:"pointer",fontSize:"13px",fontWeight:"500",transition:"background 0.2s, border-color 0.2s, color 0.2s"}}>
                  <span aria-hidden="true">👁</span> Filters
                  {hasActiveArchiveFilters() && <span style={{background:"#ff4500",color:"#fff",borderRadius:"10px",padding:"1px 6px",fontSize:"10px",fontWeight:"700"}}>ON</span>}
                  <span style={{fontSize:"10px",opacity:0.6}}>{archiveFilterBarOpen?"▲":"▼"}</span>
                </button>
                <select value={archiveSortBy} onChange={e=>{
                  const v=e.target.value; setArchiveSortBy(v)
                  applyArchiveFilters({...archiveFiltersRef.current,sort:v})
                }} style={{padding:"8px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#888",fontSize:"13px",cursor:"pointer",outline:"none"}}>
                  <option value="last_added">Last added</option>
                  <option value="newest">Reddit date ↓</option>
                  <option value="oldest">Reddit date ↑</option>
                  <option value="title_asc">Title A → Z</option>
                  <option value="title_desc">Title Z → A</option>
                </select>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                {hasActiveArchiveFilters() && (
                  <button onClick={clearArchiveFilters} style={{padding:"8px 12px",background:"#1e1e1e",border:"1px solid #ff450044",borderRadius:"8px",color:"#ff6a33",cursor:"pointer",fontSize:"12px",fontWeight:"500",whiteSpace:"nowrap"}}>✕ Clear</button>
                )}
                <div style={{position:"relative"}}>
                  <span style={{position:"absolute",left:"12px",top:"50%",transform:"translateY(-50%)",color:"#666",fontSize:"15px"}}>⌕</span>
                  <input type="search" inputMode="search" enterKeyHint="search" placeholder="Search hidden…" aria-label="Search hidden posts" autoComplete="off" spellCheck={false} value={archiveSearch} onChange={handleArchiveSearch}
                    style={{padding:"8px 12px 8px 36px",borderRadius:"20px",border:"1px solid #333",width:"200px",background:"#1a1a1a",color:"#fff",fontSize:"13px",outline:"none"}}/>
                </div>
              </div>
            </div>
            {archiveFilterBarOpen && (
              <div style={{padding:"12px 16px 16px",borderTop:"1px solid #1a1a1a"}}>
                <div style={{maxWidth:"1400px",margin:"0 auto",display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
                  <input type="text" inputMode="text" placeholder="r/ subreddit…" aria-label="Filter hidden by subreddit" autoComplete="off" spellCheck={false} value={archiveFilterSubreddit}
                    onChange={e=>{
                      const v=e.target.value; setArchiveFilterSubreddit(v)
                      clearTimeout(archiveSearchTimeout._sub)
                      archiveSearchTimeout._sub=setTimeout(()=>applyArchiveFilters({...archiveFiltersRef.current,subreddit:v}),400)
                    }}
                    style={{padding:"9px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}/>
                  <input type="text" inputMode="text" placeholder="u/ author…" aria-label="Filter hidden by author" autoComplete="off" spellCheck={false} value={archiveFilterAuthor}
                    onChange={e=>{
                      const v=e.target.value; setArchiveFilterAuthor(v)
                      clearTimeout(archiveSearchTimeout._auth)
                      archiveSearchTimeout._auth=setTimeout(()=>applyArchiveFilters({...archiveFiltersRef.current,author:v}),400)
                    }}
                    style={{padding:"9px 12px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"8px",color:"#fff",fontSize:"13px",outline:"none",width:"140px"}}/>
                  <div style={{display:"flex",alignItems:"center",gap:"6px",flexWrap:"wrap"}}>
                    {[{value:"image",label:"🖼 Images"},{value:"video",label:"🎬 Videos"},{value:"text",label:"📝 Text"}].map(mt=>(
                      <label key={mt.value} style={{display:"flex",alignItems:"center",gap:"5px",cursor:"pointer",padding:"7px 10px",background:archiveFilterMediaTypes.includes(mt.value)?"#ff450022":"#1a1a1a",borderRadius:"8px",border:"1px solid",borderColor:archiveFilterMediaTypes.includes(mt.value)?"#ff4500":"#2a2a2a",transition:"background 0.15s, color 0.15s, opacity 0.15s",minHeight:"36px"}}>
                        <input type="checkbox" checked={archiveFilterMediaTypes.includes(mt.value)} onChange={e=>{
                          const newTypes=e.target.checked?[...archiveFilterMediaTypes,mt.value]:archiveFilterMediaTypes.filter(t=>t!==mt.value)
                          setArchiveFilterMediaTypes(newTypes)
                          applyArchiveFilters({...archiveFiltersRef.current,mediaTypes:newTypes})
                        }} style={{width:"14px",height:"14px",accentColor:"#ff4500"}}/>
                        <span style={{fontSize:"12px",color:archiveFilterMediaTypes.includes(mt.value)?"#ff6a33":"#666"}}>{mt.label}</span>
                      </label>
                    ))}
                  </div>
                  <label style={{display:"flex",alignItems:"center",gap:"6px",cursor:"pointer",minHeight:"36px"}}>
                    <input type="checkbox" checked={archiveShowNsfw} onChange={e=>{
                      const v=e.target.checked; setArchiveShowNsfw(v)
                      applyArchiveFilters({...archiveFiltersRef.current,nsfw:v})
                    }} style={{width:"16px",height:"16px",accentColor:"#ff4500"}}/>
                    <span style={{fontSize:"12px",color:archiveShowNsfw?"#ff6a33":"#555",textTransform:"uppercase",letterSpacing:"0.5px"}}>NSFW</span>
                  </label>
                </div>
              </div>
            )}
          </div>
        )}

        <div style={{padding:"24px",maxWidth:"1400px",margin:"0 auto"}}>
          {archiveSearchResults && (
            <div>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"24px"}}>
                <div style={{display:"flex",alignItems:"center",gap:"12px"}}>
                  <div style={{width:"4px",height:"24px",background:"linear-gradient(180deg,#888,#555)",borderRadius:"2px"}}/>
                  <h2 style={{margin:0,fontSize:"20px",fontWeight:"600"}}>Search Results <span style={{color:"#666",fontWeight:"400"}}>({archiveSearchResults.length})</span></h2>
                </div>
                <button onClick={()=>{setArchiveSearchResults(null);setArchiveSearch("")}} style={{padding:"10px 20px",background:"#1e1e1e",border:"1px solid #333",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"14px"}}>Clear Search</button>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"16px"}}>
                {archiveSearchResults.map(p=>(
                  <div key={p.id} onClick={()=>{setGalleryIdx(0);setSelectedPost(p)}} className="post-card" style={{background:"linear-gradient(145deg,#1e1e1e,#171717)",padding:"20px",borderRadius:"14px",cursor:"pointer",border:"1px solid #2a2a2a",transition:"background 0.2s ease, color 0.2s ease, transform 0.2s ease"}}>
                    <div style={{fontSize:"11px",color:"#888",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"6px"}}>{p.subreddit?`r/${p.subreddit}`:""}</div>
                    <div style={{fontWeight:"500",marginBottom:"8px",lineHeight:"1.4",color:"#e0e0e0"}}>{p.title}</div>
                    {p.author && <div style={{fontSize:"12px",color:"#555"}}>u/{p.author}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {!archiveSearchResults && (
            <>
              {archivePosts.length===0 && !archiveIsLoading && (
                <div style={{padding:"60px",textAlign:"center",color:"#555",fontSize:"14px"}}>No hidden posts yet.</div>
              )}
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"16px"}}>
                {archivePosts.map(p=>(
                   <article key={p.id} onClick={()=>{setGalleryIdx(0);setSelectedPost(p)}}
                    onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setGalleryIdx(0);setSelectedPost(p)}}}
                    onMouseEnter={()=>setHoveredCard(p.id)} onMouseLeave={()=>setHoveredCard(null)}
                    role="button" tabIndex={0} aria-label={p.title}
                    className="post-card"
                    style={{background:"linear-gradient(145deg,#1a1a1a,#141414)",borderRadius:"16px",overflow:"hidden",cursor:"pointer",border:"1px solid #222",opacity:0.9}}>
                    {p.is_video ? (
                      <div style={{aspectRatio:"1",background:"#0a0a0a",position:"relative",overflow:"hidden"}}>
                        {hoveredCard===p.id && p.video_url && (p.video_url.includes("v.redd.it")||p.video_url.endsWith(".mp4")) ? (
                          <video src={p.video_url} autoPlay muted loop playsInline style={{width:"100%",height:"100%",objectFit:"cover"}}/>
                        ) : (
                          <div style={{width:"100%",height:"100%",display:"flex",alignItems:"center",justifyContent:"center",background:"linear-gradient(135deg,#111 0%,#1a1a1a 100%)",position:"relative"}}>
                            {(p.thumb_url||p.preview_url) && <img src={p.thumb_url||p.preview_url} alt="" loading="lazy" decoding="async" style={{position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover",opacity:0.6}} onError={e=>e.target.style.display="none"}/>}
                            <div style={{position:"relative",zIndex:1,width:"64px",height:"64px",borderRadius:"50%",background:"rgba(0,0,0,0.55)",border:"2px solid rgba(100,100,100,0.6)",display:"flex",alignItems:"center",justifyContent:"center"}}>
                              <div style={{width:0,height:0,borderTop:"12px solid transparent",borderBottom:"12px solid transparent",borderLeft:"20px solid #888",marginLeft:"4px"}}/>
                            </div>
                          </div>
                        )}
                        <div style={{position:"absolute",top:"10px",left:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"3px 8px",display:"flex",alignItems:"center",gap:"5px",fontSize:"10px",fontWeight:"700",color:"#888",letterSpacing:"0.5px",border:"1px solid rgba(255,255,255,0.08)"}}>
                          <div style={{width:0,height:0,borderTop:"5px solid transparent",borderBottom:"5px solid transparent",borderLeft:"8px solid #888"}}/>VIDEO
                        </div>
                        <div style={{position:"absolute",top:"10px",right:"10px",background:"rgba(0,0,0,0.75)",borderRadius:"4px",padding:"2px 6px",fontSize:"9px",color:"#666",fontWeight:"600"}}>ARCHIVED</div>
                        <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
                          <div style={{fontSize:"11px",color:"#888",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
                        </div>
                      </div>
                    ) : (p.url||p.image_urls?.[0]) ? (
                      <div style={{aspectRatio:"1",background:"#141414",position:"relative",overflow:"hidden"}}>
                         <img src={p.url||p.image_urls?.[0]} alt={p.title} loading="lazy" decoding="async" style={{width:"100%",height:"100%",objectFit:"cover",opacity:0.85}} onError={e=>e.target.style.display="none"}/>
                        {p.image_urls?.length>1 && (
                          <div style={{position:"absolute",top:"10px",right:"10px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"4px 10px",fontSize:"11px",fontWeight:"600",color:"#fff"}}>1/{p.image_urls.length}</div>
                        )}
                        <div style={{position:"absolute",top:"10px",left:"10px",background:"rgba(0,0,0,0.75)",borderRadius:"4px",padding:"2px 6px",fontSize:"9px",color:"#666",fontWeight:"600"}}>ARCHIVED</div>
                        <div style={{position:"absolute",bottom:0,left:0,right:0,background:"linear-gradient(transparent,rgba(0,0,0,0.8))",padding:"40px 16px 16px"}}>
                          <div style={{fontSize:"11px",color:"#888",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600"}}>{p.subreddit||"reddit"}</div>
                        </div>
                      </div>
                    ) : (
                      <div style={{padding:"24px",background:"linear-gradient(135deg,#161616 0%,#1e1e1e 100%)",minHeight:"180px",display:"flex",flexDirection:"column"}}>
                        <div style={{fontSize:"11px",color:"#888",textTransform:"uppercase",letterSpacing:"1px",fontWeight:"600",marginBottom:"12px"}}>{p.subreddit||"reddit"}</div>
                        <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4",color:"#bbb"}}>{p.title}</div>
                        {p.selftext && <div style={{fontSize:"13px",color:"#555",lineHeight:"1.6",flex:1}}>{truncateText(p.selftext)}</div>}
                      </div>
                    )}
                    <div style={{padding:"10px 14px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:"8px"}}>
                      <div style={{minWidth:0,flex:1}}>
                        <div style={{fontSize:"10px",color:"#555",textTransform:"uppercase",letterSpacing:"1px",marginBottom:"3px"}}>{p.subreddit||"reddit"}</div>
                        <div style={{fontSize:"13px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:"#888"}}>{p.title}</div>
                      </div>
                      <div style={{display:"flex",gap:"4px",flexShrink:0}}>
                        <button onClick={e=>{e.stopPropagation();deletePost(p.id)}} aria-label="Delete post"
                           style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#666",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                           <span aria-hidden="true">🗑</span>
                         </button>
                         <button onClick={e=>{e.stopPropagation();unarchivePost(p.id)}} aria-label="Unhide post"
                           style={{minWidth:"36px",minHeight:"36px",padding:"0 8px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#666",cursor:"pointer",fontSize:"14px",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s"}}>
                           <span aria-hidden="true">👁</span>
                         </button>
                       </div>
                     </div>
                   </article>
                 ))}
              </div>
              {archiveIsLoading && (
                <div style={{padding:"40px",textAlign:"center",color:"#555",fontSize:"14px"}}>
                  <span style={{width:"20px",height:"20px",border:"2px solid #333",borderTopColor:"#888",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/>
                </div>
              )}
              <div ref={archiveLoader} style={{padding:"60px",textAlign:"center",color:"#333",fontSize:"14px"}}>
                <span style={{width:"20px",height:"20px",border:"2px solid #222",borderTopColor:"#555",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/>
              </div>
            </>
          )}
        </div>
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
                 <input type="text" aria-label='Type RESET to confirm' autoComplete="off" spellCheck={false} value={resetInput} onChange={e=>setResetInput(e.target.value)}
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
                  style={{padding:"12px 24px",background:resetInput==="RESET"?"#cc0000":"#330000",border:"1px solid #550000",borderRadius:"10px",color:resetInput==="RESET"?"#fff":"#555",cursor:resetInput==="RESET"?"pointer":"not-allowed",fontSize:"14px",fontWeight:"600",transition:"background 0.2s, color 0.2s, opacity 0.2s"}}>
                  {resetLoading?"Resetting…":"Confirm Reset"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── DELETE POST MODAL ── */}
      {deleteModal && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:300,padding:"20px",backdropFilter:"blur(8px)"}} onClick={()=>setDeleteModal(false)}>
          <div style={{background:"#0d0d0d",borderRadius:"20px",maxWidth:"420px",width:"100%",border:"1px solid #550000",boxShadow:"0 24px 80px rgba(200,0,0,0.3)"}} onClick={e=>e.stopPropagation()}>
            <div style={{padding:"28px 28px 0"}}>
              <div style={{fontSize:"28px",marginBottom:"12px"}}>🗑️</div>
              <h2 style={{margin:"0 0 12px",fontSize:"22px",color:"#ff4444"}}>Delete Post</h2>
              <p style={{margin:"0 0 20px",color:"#aaa",fontSize:"14px",lineHeight:"1.6"}}>This will permanently delete this post and all its downloaded media from the database and disk.</p>
              <p style={{margin:"0 0 20px",color:"#666",fontSize:"13px"}}>The post may be re-archived on the next scrape.</p>
            </div>
            <div style={{padding:"0 28px 28px",display:"flex",gap:"10px",justifyContent:"flex-end"}}>
              <button onClick={()=>setDeleteModal(false)}
                style={{padding:"12px 24px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"10px",color:"#888",cursor:"pointer",fontSize:"14px"}}>
                Cancel
              </button>
              <button onClick={confirmDeletePost}
                style={{padding:"12px 24px",background:"#cc0000",border:"1px solid #550000",borderRadius:"10px",color:"#fff",cursor:"pointer",fontSize:"14px",fontWeight:"600"}}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

        {/* ── POST DETAIL MODAL ── */}
      {selectedPost && (
        <div 
          role="dialog" aria-modal="true" aria-label={selectedPost.title}
          style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.92)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:200,backdropFilter:"blur(12px)",WebkitBackdropFilter:"blur(12px)"}} 
          onClick={()=>setSelectedPost(null)}
        >
          <div 
            className="modal-enter"
            style={{
              background:"#0d0d0d",
              borderRadius:"20px 20px 0 0",
              width:"100%",
              maxWidth:"760px",
              maxHeight:"93vh",
              overflow:"auto",
              border:"1px solid #222",
              borderBottom:"none",
              boxShadow:"0 -8px 60px rgba(0,0,0,0.7)",
              paddingBottom:"env(safe-area-inset-bottom, 0)"
            }}
            onClick={e=>e.stopPropagation()}
            onTouchStart={handleTouchStart}
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
          >
            {/* Drag handle */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"center",padding:"12px 0 4px"}}>
              <div style={{width:"40px",height:"4px",background:"#333",borderRadius:"2px"}}/>
            </div>

            {(selectedPost.is_video || selectedPost.video_url) ? (
              <div style={{background:"#000",position:"relative",overflow:"hidden"}}>
                {selectedPost.video_url && (selectedPost.video_url.includes("v.redd.it")||selectedPost.video_url.endsWith(".mp4")) ? (
                  <video
                    src={selectedPost.video_url}
                    controls autoPlay muted loop playsInline
                    style={{width:"100%",maxHeight:"480px",display:"block",background:"#000"}}
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
                    {(selectedPost.video_urls?.[0] || selectedPost.url) && <a href={selectedPost.video_urls?.[0] || selectedPost.url} target="_blank" rel="noopener noreferrer" style={{color:"#ff4500",fontSize:"13px",textDecoration:"none"}}>↗ Open video source</a>}
                  </div>
                )}
                <div style={{position:"absolute",top:"12px",left:"12px",background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",borderRadius:"6px",padding:"4px 10px",display:"flex",alignItems:"center",gap:"6px",fontSize:"11px",fontWeight:"700",color:"#fff",border:"1px solid rgba(255,255,255,0.1)"}}>
                  <div style={{width:0,height:0,borderTop:"5px solid transparent",borderBottom:"5px solid transparent",borderLeft:"8px solid #ff4500"}}/>
                  VIDEO
                </div>
                {(selectedPost.video_urls?.[0] || selectedPost.url) && (
                  <div style={{position:"absolute",top:"12px",right:"12px"}}>
                    <a href={selectedPost.video_urls?.[0] || selectedPost.url} target="_blank" rel="noopener noreferrer" style={{background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",color:"#fff",padding:"8px 14px",borderRadius:"8px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px",border:"1px solid rgba(255,255,255,0.1)"}}>↗ Open</a>
                  </div>
                )}
              </div>
            ) : (selectedPost.url || selectedPost.image_urls?.[0]) ? (
              <div style={{background:"#000",position:"relative",userSelect:"none"}}>
                 <img 
                   src={selectedPost.image_urls?.[galleryIdx] || selectedPost.url || selectedPost.image_urls?.[0]} 
                   alt={selectedPost.title}
                   style={{width:"100%",maxHeight:"460px",objectFit:"contain",display:"block"}} 
                   onError={e=>e.target.style.display="none"}
                   draggable={false}
                />
                {/* Gallery navigation - large touch targets */}
                {selectedPost.image_urls?.length > 1 && (
                  <>
                     <button 
                       aria-label="Previous image"
                       onClick={e=>{e.stopPropagation();setGalleryIdx(i=>Math.max(0,i-1))}} 
                       disabled={galleryIdx===0}
                       style={{position:"absolute",top:"50%",left:"8px",transform:"translateY(-50%)",zIndex:10,background:"rgba(0,0,0,0.7)",backdropFilter:"blur(4px)",border:"1px solid rgba(255,255,255,0.15)",borderRadius:"50%",width:"48px",height:"48px",cursor:"pointer",fontSize:"24px",color:galleryIdx===0?"rgba(255,255,255,0.2)":"#fff",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s",WebkitTapHighlightColor:"transparent"}}>
                       <span aria-hidden="true">‹</span>
                     </button>
                     <button 
                       aria-label="Next image"
                       onClick={e=>{e.stopPropagation();setGalleryIdx(i=>Math.min(selectedPost.image_urls.length-1,i+1))}} 
                       disabled={galleryIdx===selectedPost.image_urls.length-1}
                       style={{position:"absolute",top:"50%",right:"8px",transform:"translateY(-50%)",zIndex:10,background:"rgba(0,0,0,0.7)",backdropFilter:"blur(4px)",border:"1px solid rgba(255,255,255,0.15)",borderRadius:"50%",width:"48px",height:"48px",cursor:"pointer",fontSize:"24px",color:galleryIdx===selectedPost.image_urls.length-1?"rgba(255,255,255,0.2)":"#fff",display:"flex",alignItems:"center",justifyContent:"center",transition:"background 0.15s, color 0.15s",WebkitTapHighlightColor:"transparent"}}>
                       <span aria-hidden="true">›</span>
                     </button>
                     {/* Dot indicators */}
                     <div style={{position:"absolute",bottom:"12px",left:"50%",transform:"translateX(-50%)",display:"flex",gap:"5px",zIndex:10}}>
                       {selectedPost.image_urls.slice(0, Math.min(selectedPost.image_urls.length, 10)).map((_,i)=>(
                         <button key={i} aria-label={`Go to image ${i+1}`} aria-current={i===galleryIdx?"true":undefined} onClick={e=>{e.stopPropagation();setGalleryIdx(i)}}
                           style={{width:i===galleryIdx?"20px":"7px",height:"7px",borderRadius:"4px",border:"none",background:i===galleryIdx?"#ff4500":"rgba(255,255,255,0.4)",cursor:"pointer",padding:0,transition:"width 0.25s ease, background 0.25s ease",WebkitTapHighlightColor:"transparent"}}/>
                       ))}
                      {selectedPost.image_urls.length > 10 && <span style={{color:"rgba(255,255,255,0.5)",fontSize:"10px",lineHeight:"7px"}}>+{selectedPost.image_urls.length-10}</span>}
                    </div>
                    {/* Counter badge */}
                    <div style={{position:"absolute",top:"12px",left:"50%",transform:"translateX(-50%)",background:"rgba(0,0,0,0.8)",backdropFilter:"blur(4px)",borderRadius:"10px",padding:"4px 12px",fontSize:"12px",color:"#fff",fontVariantNumeric:"tabular-nums",border:"1px solid rgba(255,255,255,0.1)"}}>
                      {galleryIdx + 1} / {selectedPost.image_urls.length}
                    </div>
                  </>
                )}
                <div style={{position:"absolute",top:"12px",right:"12px"}}>
                  <a href={selectedPost.image_urls?.[galleryIdx] || selectedPost.url} target="_blank" rel="noopener noreferrer" style={{background:"rgba(0,0,0,0.75)",backdropFilter:"blur(4px)",color:"#fff",padding:"8px 14px",borderRadius:"8px",textDecoration:"none",fontSize:"12px",display:"flex",alignItems:"center",gap:"4px",border:"1px solid rgba(255,255,255,0.1)"}}>↗ Open</a>
                </div>
              </div>
            ) : null}

            <div style={{padding:"20px 24px"}}>
              <div style={{display:"flex",gap:"12px",fontSize:"13px",color:"#666",marginBottom:"16px",flexWrap:"wrap",alignItems:"center"}}>
                <span style={{color:"#ff4500",fontWeight:"600",background:"rgba(255,69,0,0.12)",padding:"4px 10px",borderRadius:"6px",fontSize:"12px"}}>r/{selectedPost.subreddit||"reddit"}</span>
                <span style={{color:"#888",fontSize:"12px"}}>u/{selectedPost.author||"unknown"}</span>
                {selectedPost.created_utc && <span style={{color:"#444",fontSize:"11px"}}>{formatTime(selectedPost.created_utc)}</span>}
              </div>
              <h2 style={{margin:"0 0 20px",fontSize:"20px",lineHeight:"1.4",fontWeight:"600",color:"#fff"}}>{selectedPost.title}</h2>

              {selectedPost.selftext && (
                <div style={{background:"linear-gradient(145deg,#141414,#1a1a1a)",padding:"20px",borderRadius:"12px",marginBottom:"20px",fontSize:"14px",lineHeight:"1.8",color:"#bbb",whiteSpace:"pre-wrap",border:"1px solid #222",maxHeight:"240px",overflow:"auto",WebkitOverflowScrolling:"touch"}}>
                  {selectedPost.selftext}
                </div>
              )}

              {/* Actions row */}
              <div style={{marginBottom:"20px",display:"flex",gap:"8px",flexWrap:"wrap"}}>
                 <button onClick={()=>deletePost(selectedPost.id)}
                   style={{padding:"11px 18px",background:"#3a1a1a",border:"1px solid #5a2a2a",borderRadius:"10px",color:"#ff6666",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>
                   <span aria-hidden="true">🗑</span> Delete
                 </button>
                 {selectedPost.archived ? (
                   <button onClick={()=>unarchivePost(selectedPost.id)}
                     style={{padding:"11px 18px",background:"#1e3a1e",border:"1px solid #2a5a2a",borderRadius:"10px",color:"#46d160",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>
                     ↩ Unhide
                   </button>
                 ) : (
                   <button onClick={()=>archivePost(selectedPost.id)}
                     style={{padding:"11px 18px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"10px",color:"#888",cursor:"pointer",fontSize:"13px",fontWeight:"600",minHeight:"44px"}}>
                     <span aria-hidden="true">👁</span> Hide
                   </button>
                 )}
                 <button onClick={()=>setSelectedPost(null)} aria-label="Close post"
                   style={{padding:"11px 18px",background:"#1a1a1a",border:"1px solid #2a2a2a",borderRadius:"10px",color:"#555",cursor:"pointer",fontSize:"13px",marginLeft:"auto",minHeight:"44px"}}>
                   ✕ Close
                 </button>
              </div>

              {/* Comments */}
              {selectedPost.comments === undefined && (
                <div style={{color:"#444",fontSize:"13px",padding:"8px 0",display:"flex",alignItems:"center",gap:"8px"}}>
                  <span style={{width:"14px",height:"14px",border:"2px solid #333",borderTopColor:"#555",borderRadius:"50%",display:"inline-block",animation:"spin 1s linear infinite"}}/>
                  Loading comments…
                </div>
              )}
              {selectedPost.comments && selectedPost.comments.length > 0 && (
                <div>
                  <div style={{fontSize:"12px",color:"#555",fontWeight:"600",textTransform:"uppercase",letterSpacing:"0.5px",marginBottom:"12px"}}>
                    Comments ({selectedPost.comments.length})
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:"8px",maxHeight:"300px",overflow:"auto",WebkitOverflowScrolling:"touch",paddingRight:"2px"}}>
                    {selectedPost.comments.map(c=>(
                      <div key={c.id} style={{background:"#141414",borderRadius:"10px",padding:"12px",border:"1px solid #1e1e1e"}}>
                        <div style={{display:"flex",gap:"8px",alignItems:"center",marginBottom:"6px"}}>
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
          </div>
        </div>
      )}

      {/* ── TOAST NOTIFICATIONS ── */}
      <div role="status" aria-live="polite" aria-atomic="false" style={{position:"fixed",bottom:"max(24px, env(safe-area-inset-bottom, 24px))",left:"50%",transform:"translateX(-50%)",display:"flex",flexDirection:"column",gap:"8px",zIndex:1000,pointerEvents:"none",width:"min(400px, calc(100vw - 32px))"}}>
        {toasts.map(t=>(
          <div key={t.id} style={{
            background:t.type==="success"?"linear-gradient(135deg,#0d2818,#1a1a1a)":t.type==="error"?"linear-gradient(135deg,#2d0a00,#1a1a1a)":"#1e1e1e",
            border:`1px solid ${t.type==="success"?"#46d16066":t.type==="error"?"#ff450066":"#333"}`,
            color:t.type==="success"?"#46d160":t.type==="error"?"#ff6a33":"#ccc",
            padding:"12px 20px",
            borderRadius:"12px",
            fontSize:"14px",
            boxShadow:"0 8px 32px rgba(0,0,0,0.5)",
            backdropFilter:"blur(8px)",
            animation:"slideUp 0.25s ease",
            display:"flex",
            alignItems:"center",
            gap:"10px",
            pointerEvents:"auto"
          }}>
            <span style={{fontSize:"16px"}} aria-hidden="true">{t.type==="success"?"✓":t.type==="error"?"✗":"ⓘ"}</span>
            {t.message}
          </div>
        ))}
      </div>

      {/* ── PWA INSTALL BANNER ── */}
      {showInstallBanner && installPrompt && (
        <div style={{
          position:"fixed",
          bottom:"max(80px, calc(env(safe-area-inset-bottom, 0px) + 80px))",
          right:"16px",
          background:"linear-gradient(135deg,#1e1e1e,#141414)",
          border:"1px solid #ff450044",
          borderRadius:"14px",
          padding:"14px 16px",
          display:"flex",
          alignItems:"center",
          gap:"12px",
          zIndex:900,
          boxShadow:"0 8px 32px rgba(0,0,0,0.4)",
          maxWidth:"280px",
          animation:"slideUp 0.3s ease"
        }}>
          <img src="/icon.png" style={{width:"36px",height:"36px",borderRadius:"8px"}} alt=""/>
          <div style={{flex:1}}>
            <div style={{fontSize:"13px",fontWeight:"600",color:"#fff",marginBottom:"2px"}}>Install App</div>
            <div style={{fontSize:"11px",color:"#666"}}>Add to home screen</div>
          </div>
          <div style={{display:"flex",gap:"6px"}}>
            <button
              onClick={async()=>{
                installPrompt.prompt()
                const result = await installPrompt.userChoice
                setShowInstallBanner(false)
                setInstallPrompt(null)
              }}
              style={{padding:"7px 14px",background:"linear-gradient(135deg,#ff4500,#ff6a33)",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontSize:"12px",fontWeight:"600"}}>
              Install
            </button>
            <button
              onClick={()=>setShowInstallBanner(false)}
              aria-label="Dismiss install prompt"
              style={{padding:"7px 10px",background:"#1a1a1a",border:"1px solid #333",borderRadius:"8px",color:"#555",cursor:"pointer",fontSize:"12px"}}>
              <span aria-hidden="true">✕</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

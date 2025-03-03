
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

import asyncio
import queue
import time
from fastapi import HTTPException
from fastapi import status
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from hashids import Hashids
from typing import ClassVar, Literal, TypedDict

from app.constants import QUALITY_TYPES
from app.utils import Logging
from app.utils.EDCB import EDCBTuner
from app.utils.hls import LiveLLHLSSegmenter


class LiveStreamStatus(TypedDict):
    """ ライブストリームのステータスを表す辞書の型定義 """
    status: Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart']
    detail: str
    updated_at: float
    clients_count: int


class LiveStreamClient():
    """ ライブストリームのクライアントを表すクラス """


    def __init__(self, livestream: LiveStream, client_type: Literal['mpegts', 'll-hls']):
        """
        ライブストリーミングクライアントのインスタンスを初期化する
        なお、LiveStreamClient は LiveStream クラス外から初期化してはいけない
        (必ず LiveStream.connect() or LiveStream.connectToExistingClient() で取得した LiveStreamClient を利用すること)

        Args:
            livestream (LiveStream): クライアントが紐づくライブストリームのインスタンス
            client_type (Literal['mpegts', 'll-hls']): クライアントの種別 (mpegts or ll-hls)
        """

        # このクライアントが紐づくライブストリームのインスタンス
        self._livestream: LiveStream = livestream

        # クライアント ID
        ## ミリ秒単位のタイムスタンプをもとに、Hashids による10文字のユニーク ID が生成される
        self.client_id: str = ('MPEGTS-' if client_type == 'mpegts' else 'LLHLS-') + Hashids(min_length=10).encode(int(time.time() * 1000))

        # クライアントの種別 (mpegts or ll-hls)
        self.client_type: Literal['mpegts', 'll-hls'] = client_type

        # ストリームデータが入る Queue
        ## client_type が mpegts の場合のみ、クライアントが持つ Queue にストリームデータが入る
        ## client_type が ll-hls の場合は配信方式が異なるため Queue は使われない
        self.queue: queue.Queue[bytes | None] = queue.Queue()

        # ストリームデータの最終読み取り時刻のタイミング
        ## 最終読み取り時刻を10秒過ぎたクライアントは LiveStream.writeStreamData() でタイムアウトと判断され、削除される
        self.stream_data_read_at: float = time.time()


    def readStreamData(self) -> bytes | None:
        """
        自分自身の Queue からストリームデータを読み取って返す
        Queue 内のストリームデータは LiveStream.writeStreamData() で書き込まれたもの

        Args:
            client (LiveStreamClient): ライブストリームクライアントのインスタンス

        Returns:
            bytes | None: ストリームデータ (エンコードタスクが終了した場合は None が返る)
        """

        # LL-HLS クライアントの場合は実行しない
        if self.client_type == 'll-hls':
            return None

        # ストリームデータの最終読み取り時刻を更新
        self.stream_data_read_at = time.time()

        # Queue から読み取ったストリームデータを返す
        try:
            return self.queue.get_nowait()
        except queue.Empty:  # キューの中身が空
            return b''  # None にはせず、処理を継続させる
        except TypeError:
            return None


    async def __commonForLLHLSClient(self,
        response_type: Literal['Playlist', 'Segment', 'PartialSegment', 'InitializationSegment'],
        msn: int | None,
        part: int | None,
        secondary_audio: bool = False
    ) -> Response | StreamingResponse:
        """
        LL-HLS クライアント向け API の共通処理 (バリデーションと最終読み取り時刻の更新)

        Args:
            response_type (Literal['Playlist', 'Segment', 'PartialSegment', 'InitializationSegment']): レスポンスの種別
            msn (int | None): LL-HLS プレイリストの msn (Media Sequence Number) インデックス
            part (int | None): LL-HLS プレイリストの part (部分セグメント) インデックス
            secondary_audio (bool, optional): 副音声用セグメントを取得するかどうか. Defaults to False.

        Returns:
            Response | StreamingResponse: FastAPI のレスポンス
        """

        # mpegts.js クライアントの場合は実行しない
        if self.client_type == 'mpegts':
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'This API is only for LL-HLS client',
            )

        # LL-HLS Segmenter が None (=Offline) の場合は実行しない
        if self._livestream.segmenter is None:
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'LL-HLS Segmenter is not running',
            )

        # 指定されたデータのレスポンスを取得
        if response_type == 'Playlist':
            response = await self._livestream.segmenter.getPlaylist(msn, part, secondary_audio)
        elif response_type == 'Segment':
            response = await self._livestream.segmenter.getSegment(msn, secondary_audio)
        elif response_type == 'PartialSegment':
            response = await self._livestream.segmenter.getPartialSegment(msn, part, secondary_audio)
        elif response_type == 'InitializationSegment':
            response = await self._livestream.segmenter.getInitializationSegment(secondary_audio)

        # ストリームデータの最終読み取り時刻を更新
        ## LL-HLS Segmenter からのレスポンス取得後に更新しないとタイムアウト判定が正しく行われない
        self.stream_data_read_at = time.time()

        return response


    async def getPlaylist(self, msn: int | None, part: int | None, secondary_audio: bool = False) -> Response:
        """
        LL-HLS のプレイリスト (m3u8) を FastAPI のレスポンスとして返す
        ref: https://developer.apple.com/documentation/http_live_streaming/enabling_low-latency_http_live_streaming_hls

        Args:
            msn (int | None): LL-HLS プレイリストの msn (Media Sequence Number) インデックス
            part (int | None): LL-HLS プレイリストの part (部分セグメント) インデックス
            secondary_audio (bool, optional): 副音声用セグメントを取得するかどうか. Defaults to False.

        Returns:
            Response: プレイリストデータ (m3u8) の FastAPI レスポンス
        """
        return await self.__commonForLLHLSClient('Playlist', msn, part, secondary_audio)


    async def getSegment(self, msn: int | None, secondary_audio: bool = False) -> Response | StreamingResponse:
        """
        LL-HLS の完全なセグメント (m4s) を FastAPI のレスポンスとして順次返す
        ref: https://developer.apple.com/documentation/http_live_streaming/enabling_low-latency_http_live_streaming_hls

        Args:
            msn (int | None): LL-HLS セグメントの msn (Media Sequence Number) インデックス
            secondary_audio (bool, optional): 副音声用セグメントを取得するかどうか. Defaults to False.

        Returns:
            Response | StreamingResponse: セグメントデータ (m4s) の FastAPI レスポンス (StreamingResponse)
        """
        return await self.__commonForLLHLSClient('Segment', msn, None, secondary_audio)


    async def getPartialSegment(self, msn: int | None, part: int | None, secondary_audio: bool = False) -> Response | StreamingResponse:
        """
        LL-HLS の部分セグメント (m4s) を FastAPI のレスポンスとして順次返す
        ref: https://developer.apple.com/documentation/http_live_streaming/enabling_low-latency_http_live_streaming_hls

        Args:
            msn (int | None): LL-HLS セグメントの msn (Media Sequence Number) インデックス
            part (int | None): LL-HLS セグメントの part (部分セグメント) インデックス
            secondary_audio (bool, optional): 副音声用セグメントを取得するかどうか. Defaults to False.

        Returns:
            Response | StreamingResponse: 部分セグメントデータ (m4s) の FastAPI レスポンス (StreamingResponse)
        """
        return await self.__commonForLLHLSClient('PartialSegment', msn, part, secondary_audio)


    async def getInitializationSegment(self, secondary_audio: bool = False) -> Response:
        """
        LL-HLS の初期セグメント (init) を FastAPI のレスポンスとして返す
        ref: https://developer.apple.com/documentation/http_live_streaming/enabling_low-latency_http_live_streaming_hls

        Args:
            secondary_audio (bool, optional): 副音声用セグメントを取得するかどうか. Defaults to False.

        Returns:
            Response: 初期セグメントデータ (m4s) の FastAPI レスポンス
        """
        return await self.__commonForLLHLSClient('InitializationSegment', None, None, secondary_audio)


class LiveStream():
    """ ライブストリームを管理するクラス """

    # ライブストリームのインスタンスが入る、ライブストリーム ID をキーとした辞書
    # この辞書にライブストリームに関する全てのデータが格納されている
    __instances: ClassVar[dict[str, LiveStream]] = {}


    # 必ずライブストリーム ID ごとに1つのインスタンスになるように (Singleton)
    # ref: https://qiita.com/ttsubo/items/c4af71ceba15b5b213f8
    def __new__(cls, channel_id: str, quality: QUALITY_TYPES) -> LiveStream:

        # まだ同じライブストリーム ID のインスタンスがないときだけ、インスタンスを生成する
        # (チャンネルID)-(映像の品質) で一意な ID になる
        livestream_id = f'{channel_id}-{quality}'
        if livestream_id not in cls.__instances:

            # 新しいライブストリームのインスタンスを生成する
            instance = super(LiveStream, cls).__new__(cls)

            # ライブストリーム ID を設定
            instance.livestream_id = livestream_id

            # チャンネル ID と映像の品質を設定
            instance.channel_id = channel_id
            instance.quality = quality

            # ストリームのステータス
            ## Offline, Standby, ONAir, Idling, Restart のいずれか
            instance._status = 'Offline'

            # ストリームのステータス詳細
            instance._detail = 'ライブストリームは Offline です。'

            # ストリームの開始時刻
            instance._started_at = time.time()

            # ストリームのステータスの最終更新時刻のタイムスタンプ
            instance._updated_at = time.time()

            # ストリームデータの最終書き込み時刻のタイムスタンプ
            ## 最終書き込み時刻が 5 秒 (ONAir 時) 20 秒 (Standby 時) 以上更新されていない場合は、
            ## エンコーダーがフリーズしたものとみなしてエンコードタスクを再起動する
            instance.stream_data_written_at = time.time()

            # LL-HLS Segmenter のインスタンス
            ## iPhone Safari は mpegts.js でのストリーミングに対応していないため、フォールバックとして LL-HLS で配信する必要がある
            ## エンコードタスクが実行されたときに毎回生成され、エンコードタスクが終了したときに破棄される
            instance.segmenter = None

            # EDCB バックエンドのチューナーインスタンス
            ## Mirakurun バックエンドを使っている場合は None のまま
            instance.tuner = None

            # ライブストリームクライアントが入るリスト
            ## クライアントの接続が切断された場合、このリストからも削除される
            ## したがって、クライアントの数はこのリストの長さで求められる
            instance.clients = []

            # 生成したインスタンスを登録する
            ## インスタンスの参照が渡されるので、オブジェクトとしては同一
            cls.__instances[livestream_id] = instance

        # 登録されているインスタンスを返す
        return cls.__instances[livestream_id]


    def __init__(self, channel_id: str, quality: str):
        """
        ライブストリームのインスタンスを取得する

        Args:
            channel_id (str): チャンネルID
            quality (str): 映像の品質 (1080p-60fps ~ 240p)
        """

        # インスタンス変数の型ヒントを定義
        # Singleton のためインスタンスの生成は __new__() で行うが、__init__() も定義しておかないと補完がうまく効かない
        self.livestream_id: str
        self.channel_id: str
        self.quality: QUALITY_TYPES
        self._status: Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart']
        self._detail: str
        self._started_at: float
        self._updated_at: float
        self.stream_data_written_at: float
        self.segmenter: LiveLLHLSSegmenter | None
        self.tuner: EDCBTuner | None
        self.clients: list[LiveStreamClient]


    @classmethod
    def getAllLiveStreams(cls) -> list[LiveStream]:
        """
        全てのライブストリームのインスタンスを取得する

        Returns:
            list[LiveStream]: ライブストリームのインスタンスの入ったリスト
        """

        # __instances 辞書を values() で値だけのリストにしたものを返す
        return list(cls.__instances.values())


    @classmethod
    def getONAirLiveStreams(cls) -> list[LiveStream]:
        """
        現在 ONAir なライブストリームのインスタンスを取得する

        Returns:
            list[LiveStream]: 現在 ONAir なライブストリームのインスタンスの入ったリスト
        """

        result: list[LiveStream] = []

        # 現在 ONAir 状態のライブストリームを探す
        # 見つかったら、そのライブストリームのインスタンスをリストに入れる
        for livestream in LiveStream.getAllLiveStreams():
            if livestream.getStatus()['status'] == 'ONAir':
                result.append(livestream)

        return result


    @classmethod
    def getIdlingLiveStreams(cls) -> list[LiveStream]:
        """
        現在 Idling なライブストリームのインスタンスを取得する

        Returns:
            list[LiveStream]: 現在 Idling なライブストリームのインスタンスの入ったリスト
        """

        result: list[LiveStream] = []

        # 現在 Idling 状態のライブストリームを探す
        # 見つかったら、そのライブストリームのインスタンスをリストに入れる
        for livestream in LiveStream.getAllLiveStreams():
            if livestream.getStatus()['status'] == 'Idling':
                result.append(livestream)

        return result


    @classmethod
    def getViewers(cls, channel_id:str) -> int:
        """
        指定されたチャンネルのライブストリームの現在の視聴者数を取得する

        Args:
            channel_id (str): チャンネルID

        Returns:
            int: 視聴者数
        """

        # 指定されたチャンネル ID が含まれるライブストリームを探す
        viewers = 0
        for livestream in LiveStream.getAllLiveStreams():
            if livestream.channel_id == channel_id:
                # 足していく
                viewers += livestream.getStatus()['clients_count']

        # カウントした視聴者数を返す
        return viewers


    async def connect(self, client_type: Literal['mpegts', 'll-hls']) -> LiveStreamClient:
        """
        ライブストリームに接続して、新しくライブストリームに登録されたクライアントを返す
        この時点でライブストリームが Offline ならば、新たにエンコードタスクが起動される

        Args:
            client_type (Literal['mpegts', 'll-hls']): クライアントの種別 (mpegts or ll-hls)

        Returns:
            LiveStreamClient: ライブストリームクライアントのインスタンス
        """

        # ***** ステータスの切り替え *****

        # ライブストリームが Offline な場合、新たにエンコードタスクを起動する
        if self._status == 'Offline':

            # 現在 Idling 状態のライブストリームがあれば、うち最初のライブストリームを Offline にする
            ## 一般にチューナーリソースは無尽蔵にあるわけではないので、現在 Idling（=つまり誰も見ていない）ライブストリームがあるのなら
            ## それを Offline にしてチューナーリソースを解放し、新しいライブストリームがチューナーを使えるようにする
            for _ in range(8):  # 画質切り替えなどタイミングの問題で Idling なストリームがない事もあるので、8回くらいリトライする

                # 現在 Idling 状態のライブストリームがあれば
                idling_livestreams = self.getIdlingLiveStreams()
                if len(idling_livestreams) > 0:
                    idling_livestream: LiveStream = idling_livestreams[0]

                    # EDCB バックエンドの場合はチューナーをアンロックし、これから開始するエンコードタスクで再利用できるようにする
                    if idling_livestream.tuner is not None:
                        idling_livestream.tuner.unlock()

                    # チューナーリソースを解放する
                    idling_livestream.setStatus('Offline', '新しいライブストリームが開始されたため、チューナーリソースを解放しました。')
                    break

                # 現在 ONAir 状態のライブストリームがなく、リトライした所で Idling なライブストリームが取得できる見込みがない
                if len(self.getONAirLiveStreams()) == 0:
                    break

                await asyncio.sleep(0.1)

            # ステータスを Standby に設定
            # タイミングの関係でこっちで明示的に設定しておく必要がある
            self.setStatus('Standby', 'エンコードタスクを起動しています…')

            # エンコードタスクを非同期で実行
            ## 相互に依存し合っている場合、__init__.py でモジュール内の各クラスのインポートを定義している以上うまくいかないため、
            ## どちらかをモジュールの初回参照時にインポートされないようにする必要がある
            from app.tasks import LiveEncodingTask
            instance = LiveEncodingTask(self)
            asyncio.create_task(instance.run())

        # ***** クライアントの登録 *****

        # ライブストリームクライアントのインスタンスを生成・登録する
        client = LiveStreamClient(self, client_type)
        self.clients.append(client)
        Logging.info(f'[Live: {self.livestream_id}] Client Connected. Client ID: {client.client_id}')

        # ***** アイドリングからの復帰 *****

        # ライブストリームが Idling 状態な場合、ONAir 状態に戻す（アイドリングから復帰）
        if self._status == 'Idling':
            self.setStatus('ONAir', 'ライブストリームは ONAir です。')

        # ライブストリームクライアントのインスタンスを返す
        return client


    async def connectToExistingClient(self, client_id: str) -> LiveStreamClient | None:
        """
        指定されたクライアント ID に紐づく、ライブストリームに接続済みのクライアントを取得する

        Args:
            client_id (str): ライブストリームクライアントのクライアント ID

        Returns:
            LiveStreamClient | None: ライブストリームクライアントのインスタンス (見つからなかった場合は None を返す)
        """

        # 指定されたクライアント ID のクライアントを取得する
        for client in self.clients:
            if client.client_id == client_id:
                return client

        # 見つからなかった場合は None を返す
        return None


    async def disconnect(self, client: LiveStreamClient) -> None:
        """
        指定されたクライアントのライブストリームへの接続を切断する
        LiveStreamClient を使い終わったら必ず呼び出すこと (さもなければ誰も見てないのにエンコードタスクがずっと実行され続けてしまう)

        Args:
            client (LiveStreamClient): ライブストリームクライアントのインスタンス
        """

        # 指定されたライブストリームクライアントを削除する
        ## すでにタイムアウトなどで削除されていたら何もしない
        try:
            self.clients.remove(client)
            Logging.info(f'[Live: {self.livestream_id}] Client Disconnected. Client ID: {client.client_id}')
        except ValueError:
            return


    def getStatus(self) -> LiveStreamStatus:
        """
        ライブストリームのステータスを取得する

        Returns:
            LiveStreamStatus: ライブストリームのステータスが入った辞書
        """

        # ステータス・詳細・最終更新時刻・クライアント数を返す
        return {
            'status': self._status,
            'detail': self._detail,
            'updated_at': self._updated_at,
            'clients_count': len(self.clients),
        }


    def setStatus(self, status: Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart'], detail: str, quiet: bool = False) -> None:
        """
        ライブストリームのステータスを設定する

        Args:
            status (Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart']): ライブストリームのステータス
            detail (str): ステータスの詳細
            quiet (bool): ステータス設定のログを出力するかどうか
        """

        # ステータスも詳細も現在の状態と重複しているなら、更新を行わない（同じ内容のイベントが複数発生するのを防ぐ）
        if self._status == status and self._detail == detail:
            return

        # ストリーム開始 (Offline or Restart → Standby) 時、started_at と stream_data_written_at を更新する
        # ここで更新しておかないと、いつまで経っても初期化時の古いタイムスタンプが使われてしまう
        if ((self._status == 'Offline' or self._status == 'Restart') and status == 'Standby'):
            self._started_at = time.time()
            self.stream_data_written_at = time.time()

        # ログを表示
        if quiet is False:
            Logging.info(f'[Live: {self.livestream_id}] [Status: {status}] {detail}')

        # ストリーム起動 (Standby → ONAir) 時、起動時間のログを表示する
        if self._status == 'Standby' and status == 'ONAir':
            Logging.info(f'[Live: {self.livestream_id}] Startup complete. ({round(time.time() - self._started_at, 2)} sec)')

        # ステータスと詳細を設定
        self._status = status
        self._detail = detail

        # 最終更新のタイムスタンプを更新
        self._updated_at = time.time()

        # チューナーインスタンスが存在する場合のみ
        if self.tuner is not None:

            # Idling への切り替え時、チューナーをアンロックして再利用できるように
            if self._status == 'Idling':
                self.tuner.unlock()

            # ONAir への切り替え（復帰）時、再びチューナーをロックして制御を横取りされないように
            if self._status == 'ONAir':
                self.tuner.lock()


    def writeStreamData(self, stream_data: bytes) -> None:
        """
        接続している全ての mpegts クライアントの Queue にストリームデータを書き込む
        同時にストリームデータの最終書き込み時刻を更新し、クライアントがタイムアウトしていたら削除する

        Args:
            stream_data (bytes): 書き込むストリームデータ
        """

        # 書き込み時刻
        now = time.time()

        # 接続している全てのクライアントの Queue にストリームデータを書き込む
        for client in self.clients:

            # 最終読み取り時刻を10秒過ぎたクライアントはタイムアウトと判断し、クライアントを削除する
            # 主にネットワークが切断されたなどの理由で発生する
            # Queue の読み取りはノンブロッキングなので、Standby の際にタイムスタンプが更新されなくなる心配をする必要はない
            if now - client.stream_data_read_at > 10:
                self.clients.remove(client)
                Logging.info(f'[Live: {self.livestream_id}] Client Disconnected (Timeout). Client ID: {client.client_id}')

            # ストリームデータを書き込む (クライアント種別が mpegts の場合のみ)
            if client.client_type == 'mpegts':
                client.queue.put(stream_data)

        # ストリームデータが空でなければ、最終書き込み時刻を更新
        if stream_data != b'':
            self.stream_data_written_at = now
